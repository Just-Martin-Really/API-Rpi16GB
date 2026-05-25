const std = @import("std");
const c = @cImport(@cInclude("time.h"));

const rsa = std.crypto.Certificate.rsa;
const Sha256 = std.crypto.hash.sha2.Sha256;

// base64url no-pad (RFC 4648 §5)
const b64url_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".*;
const b64dec = std.base64.Base64Decoder.init(b64url_alphabet, null);

/// Tolerance applied to the `exp` claim. A token whose declared expiry is
/// up to this many seconds in the past is still accepted. Covers small
/// drift between the Keycloak host clock and the backend host clock
/// (notably right after a Pi reboot before NTP has caught up).
pub const clock_skew_seconds: i64 = 30;

pub const VerifyError = error{
    MissingBearer,
    MalformedToken,
    UnsupportedAlgorithm,
    UnknownKid,
    InvalidSignature,
    Expired,
    WrongIssuer,
    WrongAudience,
    MissingRole,
    JwksFetchFailed,
    OutOfMemory,
};

/// One RSA public key entry from a JWKS document.
pub const Key = struct {
    kid: []const u8,
    /// Big-endian RSA modulus, with leading zero bytes stripped to the first
    /// non-zero byte (matches `Certificate.rsa.PublicKey.fromBytes` expectations).
    modulus: []const u8,
    /// Big-endian RSA public exponent (typically 3 bytes for 65537).
    exponent: []const u8,
};

/// JWT verifier backed by a Keycloak JWKS endpoint.
///
/// Owns a cache of public keys keyed by `kid`. The cache can be primed via
/// `installKey` (tests) or `refreshKeys` (production). On an unknown `kid`,
/// `verify` triggers a single refresh before giving up.
/// Minimal spin mutex. Sufficient because contention on the key cache is
/// rare: prefetch at startup, lazy refresh only on an unknown `kid`.
const SpinMutex = struct {
    state: std.atomic.Value(u8) = .{ .raw = 0 },

    fn lock(self: *SpinMutex) void {
        while (self.state.cmpxchgWeak(0, 1, .acquire, .monotonic) != null) {
            std.atomic.spinLoopHint();
        }
    }

    fn unlock(self: *SpinMutex) void {
        self.state.store(0, .release);
    }
};

pub const Verifier = struct {
    allocator: std.mem.Allocator,
    jwks_url: []const u8,
    issuer: []const u8,
    keys_arena: std.heap.ArenaAllocator,
    keys: std.ArrayList(Key),
    mutex: SpinMutex,

    pub fn init(
        allocator: std.mem.Allocator,
        jwks_url: []const u8,
        issuer: []const u8,
    ) Verifier {
        return .{
            .allocator = allocator,
            .jwks_url = jwks_url,
            .issuer = issuer,
            .keys_arena = std.heap.ArenaAllocator.init(allocator),
            .keys = .empty,
            .mutex = .{},
        };
    }

    pub fn deinit(self: *Verifier) void {
        self.keys.deinit(self.allocator);
        self.keys_arena.deinit();
    }

    /// Test hook: install a public key directly, bypassing JWKS fetch.
    /// `modulus` and `exponent` are raw big-endian byte slices (already
    /// base64url-decoded). Caller's memory is copied into the verifier.
    pub fn installKey(
        self: *Verifier,
        kid: []const u8,
        modulus: []const u8,
        exponent: []const u8,
    ) !void {
        self.mutex.lock();
        defer self.mutex.unlock();
        try self.appendKeyLocked(kid, modulus, exponent);
    }

    fn appendKeyLocked(
        self: *Verifier,
        kid: []const u8,
        modulus: []const u8,
        exponent: []const u8,
    ) !void {
        const arena = self.keys_arena.allocator();
        const k = Key{
            .kid = try arena.dupe(u8, kid),
            .modulus = try arena.dupe(u8, modulus),
            .exponent = try arena.dupe(u8, exponent),
        };
        try self.keys.append(self.allocator, k);
    }

    /// Fetch JWKS from `jwks_url` and replace the cache.
    /// `io` is the Zig 0.16 Io handle (required by `std.http.Client`).
    pub fn refreshKeys(self: *Verifier, io: std.Io) VerifyError!void {
        var client = std.http.Client{ .allocator = self.allocator, .io = io };
        defer client.deinit();

        var aw = std.Io.Writer.Allocating.init(self.allocator);
        defer aw.deinit();

        const result = client.fetch(.{
            .location = .{ .url = self.jwks_url },
            .response_writer = &aw.writer,
        }) catch return error.JwksFetchFailed;
        if (result.status != .ok) return error.JwksFetchFailed;

        const body = aw.writer.buffer[0..aw.writer.end];
        try self.parseJwks(body);
    }

    fn parseJwks(self: *Verifier, json_bytes: []const u8) VerifyError!void {
        var parsed = std.json.parseFromSlice(
            JwksDoc,
            self.allocator,
            json_bytes,
            .{ .ignore_unknown_fields = true },
        ) catch return error.JwksFetchFailed;
        defer parsed.deinit();

        self.mutex.lock();
        defer self.mutex.unlock();

        _ = self.keys_arena.reset(.retain_capacity);
        self.keys.clearRetainingCapacity();

        for (parsed.value.keys) |entry| {
            if (!std.mem.eql(u8, entry.kty, "RSA")) continue;
            if (entry.alg) |alg| {
                if (!std.mem.eql(u8, alg, "RS256")) continue;
            }

            const arena = self.keys_arena.allocator();
            const modulus = decodeBase64Url(arena, entry.n) catch continue;
            const exponent = decodeBase64Url(arena, entry.e) catch continue;
            const modulus_stripped = stripLeadingZeros(modulus);

            self.appendKeyLocked(entry.kid, modulus_stripped, exponent) catch
                return error.OutOfMemory;
        }
    }

    /// Verify an `Authorization: Bearer <jwt>` header against this verifier.
    /// Required checks: RS256 signature, issuer match, exp in the future,
    /// `aud` or `azp` matches `expected_audience`, and `required_role` is
    /// present in `realm_access.roles`.
    /// `io` may be null in tests that pre-install keys; production callers
    /// must supply it so an unknown `kid` can trigger a JWKS refresh.
    pub fn verify(
        self: *Verifier,
        io: ?std.Io,
        authorization_header: []const u8,
        expected_audience: []const u8,
        required_role: []const u8,
    ) VerifyError!void {
        return self.verifyAt(io, authorization_header, expected_audience, required_role, @as(i64, c.time(null)));
    }

    /// Same as `verify` but with an injectable clock so tests can pin a
    /// time relative to fixture token `exp` values. Production code uses
    /// `verify`, which calls this with libc `time(null)`.
    pub fn verifyAt(
        self: *Verifier,
        io: ?std.Io,
        authorization_header: []const u8,
        expected_audience: []const u8,
        required_role: []const u8,
        now: i64,
    ) VerifyError!void {
        const prefix = "Bearer ";
        if (!std.mem.startsWith(u8, authorization_header, prefix)) return error.MissingBearer;
        const token = authorization_header[prefix.len..];

        const parts = splitJwt(token) orelse return error.MalformedToken;

        // Header: extract `kid` and check `alg`.
        var header_buf: [512]u8 = undefined;
        const header_json = decodeIntoBuf(&header_buf, parts.header_b64) catch
            return error.MalformedToken;
        const header = parseJwtHeader(header_json) catch return error.MalformedToken;
        if (!std.mem.eql(u8, header.alg, "RS256")) return error.UnsupportedAlgorithm;

        // Key lookup; on miss, refresh once and retry.
        // Copy the key material into stack-local buffers under the mutex so
        // a concurrent refreshKeys (which resets keys_arena) cannot turn the
        // slices into a use-after-free while we are mid-verifyRs256.
        var modulus_buf: [512]u8 = undefined;   // up to RSA-4096 modulus
        var exponent_buf: [16]u8 = undefined;   // exponent is almost always 65537 (3 bytes)
        var found = self.copyKey(header.kid, &modulus_buf, &exponent_buf);
        if (found == null) {
            if (io) |io_h| {
                self.refreshKeys(io_h) catch return error.UnknownKid;
                found = self.copyKey(header.kid, &modulus_buf, &exponent_buf);
            }
        }
        const found_key = found orelse return error.UnknownKid;
        const key = Key{
            .kid = "",
            .modulus = modulus_buf[0..found_key.modulus_len],
            .exponent = exponent_buf[0..found_key.exponent_len],
        };

        // Signature.
        const signing_input = token[0 .. parts.header_b64.len + 1 + parts.payload_b64.len];
        var sig_buf: [768]u8 = undefined; // up to 4096-bit RSA = 512 bytes
        const sig = decodeIntoBuf(&sig_buf, parts.signature_b64) catch
            return error.InvalidSignature;
        verifyRs256(signing_input, sig, key) catch return error.InvalidSignature;

        // Payload claims.
        var payload_buf: [4096]u8 = undefined;
        const payload_json = decodeIntoBuf(&payload_buf, parts.payload_b64) catch
            return error.MalformedToken;
        const claims = parseClaims(payload_json) catch return error.MalformedToken;

        // Allow a small clock-skew window on exp. Pi RTCs drift and NTP can
        // be slow to converge after a power cut; 30 s matches the controller
        // token-refresh margin so a token accepted at issue-time stays usable
        // up to its real expiry across both clocks.
        // Compare via `now - skew` instead of `exp + skew` so an attacker
        // who controls exp cannot trip an i64 overflow with maxInt.
        if (claims.exp <= now - clock_skew_seconds) return error.Expired;
        if (!std.mem.eql(u8, claims.iss, self.issuer)) return error.WrongIssuer;

        if (!claims.matchesAudience(expected_audience)) return error.WrongAudience;
        if (!claims.hasRole(required_role)) return error.MissingRole;
    }

    /// Test-only / introspection: look up a key by kid. The returned slices
    /// point into the verifier's arena and become invalid after the next
    /// refreshKeys. Production callers must use copyKey instead.
    fn findKey(self: *Verifier, kid: []const u8) ?Key {
        self.mutex.lock();
        defer self.mutex.unlock();
        for (self.keys.items) |k| {
            if (std.mem.eql(u8, k.kid, kid)) return k;
        }
        return null;
    }

    /// Look up a key by kid and copy its modulus/exponent into caller-owned
    /// buffers under the lock. Returns the byte counts written, or null if
    /// the kid is unknown or either buffer is too small. Safe to use across
    /// a subsequent refreshKeys.
    pub const KeyLengths = struct {
        modulus_len: usize,
        exponent_len: usize,
    };
    fn copyKey(
        self: *Verifier,
        kid: []const u8,
        modulus_out: []u8,
        exponent_out: []u8,
    ) ?KeyLengths {
        self.mutex.lock();
        defer self.mutex.unlock();
        for (self.keys.items) |k| {
            if (!std.mem.eql(u8, k.kid, kid)) continue;
            if (k.modulus.len > modulus_out.len) return null;
            if (k.exponent.len > exponent_out.len) return null;
            @memcpy(modulus_out[0..k.modulus.len], k.modulus);
            @memcpy(exponent_out[0..k.exponent.len], k.exponent);
            return .{ .modulus_len = k.modulus.len, .exponent_len = k.exponent.len };
        }
        return null;
    }
};

const JwksDoc = struct {
    keys: []const JwksEntry,
};

const JwksEntry = struct {
    kty: []const u8,
    kid: []const u8,
    n: []const u8,
    e: []const u8,
    alg: ?[]const u8 = null,
    use: ?[]const u8 = null,
};

const JwtParts = struct {
    header_b64: []const u8,
    payload_b64: []const u8,
    signature_b64: []const u8,
};

fn splitJwt(token: []const u8) ?JwtParts {
    const dot1 = std.mem.indexOfScalar(u8, token, '.') orelse return null;
    const rest = token[dot1 + 1 ..];
    const dot2 = std.mem.indexOfScalar(u8, rest, '.') orelse return null;
    return .{
        .header_b64 = token[0..dot1],
        .payload_b64 = rest[0..dot2],
        .signature_b64 = rest[dot2 + 1 ..],
    };
}

const JwtHeader = struct {
    alg: []const u8,
    kid: []const u8,
};

fn parseJwtHeader(json_bytes: []const u8) !JwtHeader {
    const alg = extractStringField(json_bytes, "alg") orelse return error.MissingAlg;
    const kid = extractStringField(json_bytes, "kid") orelse return error.MissingKid;
    return .{ .alg = alg, .kid = kid };
}

const Claims = struct {
    iss: []const u8,
    exp: i64,
    aud: AudienceField,
    azp: ?[]const u8,
    realm_roles: []const []const u8,

    fn matchesAudience(self: Claims, expected: []const u8) bool {
        if (self.azp) |azp| {
            if (std.mem.eql(u8, azp, expected)) return true;
        }
        switch (self.aud) {
            .single => |s| return std.mem.eql(u8, s, expected),
            .multi => |xs| {
                for (xs) |x| if (std.mem.eql(u8, x, expected)) return true;
                return false;
            },
            .absent => return false,
        }
    }

    fn hasRole(self: Claims, role: []const u8) bool {
        for (self.realm_roles) |r| if (std.mem.eql(u8, r, role)) return true;
        return false;
    }
};

const AudienceField = union(enum) {
    absent: void,
    single: []const u8,
    multi: []const []const u8,
};

threadlocal var claims_buf: [4096]u8 = undefined;

fn parseClaims(json_bytes: []const u8) !Claims {
    // Minimal hand parser to keep claims valid across allocator lifetimes and
    // to dodge the JSON parser's discriminated-union handling for `aud`.
    // Copy bytes into a thread-local buffer so the returned slices outlive
    // the caller's stack frame for the duration of one verify() call.
    if (json_bytes.len > claims_buf.len) return error.TooLong;
    @memcpy(claims_buf[0..json_bytes.len], json_bytes);
    const buf = claims_buf[0..json_bytes.len];

    var claims: Claims = .{
        .iss = "",
        .exp = 0,
        .aud = .absent,
        .azp = null,
        .realm_roles = &.{},
    };

    claims.iss = extractStringField(buf, "iss") orelse return error.MissingIss;
    claims.exp = extractIntField(buf, "exp") orelse return error.MissingExp;
    claims.azp = extractStringField(buf, "azp");

    if (findKey(buf, "aud")) |idx| {
        // Either "aud":"..." or "aud":["...",...]
        var i = idx;
        while (i < buf.len and buf[i] != ':') i += 1;
        i += 1;
        while (i < buf.len and (buf[i] == ' ' or buf[i] == '\t')) i += 1;
        if (i < buf.len and buf[i] == '"') {
            const end = std.mem.indexOfScalarPos(u8, buf, i + 1, '"') orelse return error.MalformedAud;
            claims.aud = .{ .single = buf[i + 1 .. end] };
        } else if (i < buf.len and buf[i] == '[') {
            const arr_end = std.mem.indexOfScalarPos(u8, buf, i, ']') orelse return error.MalformedAud;
            claims.aud = .{ .multi = try splitStringArray(buf[i + 1 .. arr_end], &aud_slots) };
        }
    }

    if (findKey(buf, "realm_access")) |idx_ra| {
        // Need "realm_access":{ "roles":["...","..."] }
        if (findKeyAfter(buf, "roles", idx_ra)) |idx_roles| {
            var i = idx_roles;
            while (i < buf.len and buf[i] != '[') i += 1;
            const arr_end = std.mem.indexOfScalarPos(u8, buf, i, ']') orelse return error.MalformedRoles;
            claims.realm_roles = try splitStringArray(buf[i + 1 .. arr_end], &role_slots);
        }
    }

    return claims;
}

// Two separate slot buffers so parsing aud-as-array (into aud_slots) and
// realm_roles (into role_slots) cannot alias. Previously both used the same
// buffer, which silently overwrote claims.aud.multi when realm_roles was
// parsed afterwards. Bumped from 16 to 32 to leave headroom for the
// Keycloak default roles plus composites.
threadlocal var aud_slots: [32][]const u8 = undefined;
threadlocal var role_slots: [32][]const u8 = undefined;

fn splitStringArray(arr_body: []const u8, out: [][]const u8) ![]const []const u8 {
    var n: usize = 0;
    var i: usize = 0;
    while (i < arr_body.len) {
        while (i < arr_body.len and arr_body[i] != '"') i += 1;
        if (i >= arr_body.len) break;
        const start = i + 1;
        const end = std.mem.indexOfScalarPos(u8, arr_body, start, '"') orelse return error.MalformedArray;
        if (n >= out.len) return error.TooManyEntries;
        out[n] = arr_body[start..end];
        n += 1;
        i = end + 1;
    }
    return out[0..n];
}

fn findKey(buf: []const u8, key: []const u8) ?usize {
    var pattern_buf: [64]u8 = undefined;
    if (key.len + 2 > pattern_buf.len) return null;
    pattern_buf[0] = '"';
    @memcpy(pattern_buf[1 .. 1 + key.len], key);
    pattern_buf[1 + key.len] = '"';
    return std.mem.indexOf(u8, buf, pattern_buf[0 .. 2 + key.len]);
}

fn findKeyAfter(buf: []const u8, key: []const u8, from: usize) ?usize {
    var pattern_buf: [64]u8 = undefined;
    if (key.len + 2 > pattern_buf.len) return null;
    pattern_buf[0] = '"';
    @memcpy(pattern_buf[1 .. 1 + key.len], key);
    pattern_buf[1 + key.len] = '"';
    return std.mem.indexOfPos(u8, buf, from, pattern_buf[0 .. 2 + key.len]);
}

fn extractStringField(buf: []const u8, key: []const u8) ?[]const u8 {
    const idx = findKey(buf, key) orelse return null;
    var i = idx;
    while (i < buf.len and buf[i] != ':') i += 1;
    i += 1;
    while (i < buf.len and (buf[i] == ' ' or buf[i] == '\t')) i += 1;
    if (i >= buf.len or buf[i] != '"') return null;
    const start = i + 1;
    const end = std.mem.indexOfScalarPos(u8, buf, start, '"') orelse return null;
    return buf[start..end];
}

fn extractIntField(buf: []const u8, key: []const u8) ?i64 {
    const idx = findKey(buf, key) orelse return null;
    var i = idx;
    while (i < buf.len and buf[i] != ':') i += 1;
    i += 1;
    while (i < buf.len and (buf[i] == ' ' or buf[i] == '\t')) i += 1;
    const start = i;
    while (i < buf.len and buf[i] >= '0' and buf[i] <= '9') i += 1;
    if (i == start) return null;
    return std.fmt.parseInt(i64, buf[start..i], 10) catch null;
}

fn decodeBase64Url(allocator: std.mem.Allocator, src: []const u8) ![]u8 {
    const dec_len = try b64dec.calcSizeForSlice(src);
    const out = try allocator.alloc(u8, dec_len);
    try b64dec.decode(out, src);
    return out;
}

fn decodeIntoBuf(buf: []u8, src: []const u8) ![]const u8 {
    const dec_len = try b64dec.calcSizeForSlice(src);
    if (dec_len > buf.len) return error.TooLong;
    try b64dec.decode(buf[0..dec_len], src);
    return buf[0..dec_len];
}

fn stripLeadingZeros(bytes: []const u8) []const u8 {
    var i: usize = 0;
    while (i < bytes.len and bytes[i] == 0) i += 1;
    return bytes[i..];
}

fn verifyRs256(signing_input: []const u8, sig: []const u8, key: Key) !void {
    const public_key = try rsa.PublicKey.fromBytes(key.exponent, key.modulus);
    if (sig.len != key.modulus.len) return error.InvalidSignature;
    switch (sig.len) {
        inline 128, 256, 384, 512 => |modulus_len| {
            try rsa.PKCS1v1_5Signature.verify(
                modulus_len,
                sig[0..modulus_len].*,
                signing_input,
                public_key,
                Sha256,
            );
        },
        else => return error.UnsupportedModulusLength,
    }
}

// ============================ Tests ============================

const testing = std.testing;

test "splitJwt: rejects 2-part token" {
    try testing.expect(splitJwt("a.b") == null);
}

test "splitJwt: rejects 1-part token" {
    try testing.expect(splitJwt("onlyone") == null);
}

test "splitJwt: parses 3-part token" {
    const parts = splitJwt("header.payload.sig").?;
    try testing.expectEqualStrings("header", parts.header_b64);
    try testing.expectEqualStrings("payload", parts.payload_b64);
    try testing.expectEqualStrings("sig", parts.signature_b64);
}

test "extractStringField: simple string" {
    const json = "{\"iss\":\"https://example.com\",\"sub\":\"u1\"}";
    try testing.expectEqualStrings("https://example.com", extractStringField(json, "iss").?);
    try testing.expectEqualStrings("u1", extractStringField(json, "sub").?);
}

test "extractStringField: missing key" {
    try testing.expect(extractStringField("{\"a\":\"b\"}", "missing") == null);
}

test "extractIntField: positive int" {
    try testing.expectEqual(@as(i64, 1234567890), extractIntField("{\"exp\":1234567890}", "exp").?);
}

test "extractIntField: missing key" {
    try testing.expect(extractIntField("{\"a\":1}", "missing") == null);
}

test "parseClaims: full Keycloak-shaped payload" {
    const json =
        \\{"iss":"https://www.lab.local/auth/realms/iot","exp":4000000000,"aud":"dashboard-client","azp":"dashboard-client","realm_access":{"roles":["dashboard-user","offline_access"]}}
    ;
    const claims = try parseClaims(json);
    try testing.expectEqualStrings("https://www.lab.local/auth/realms/iot", claims.iss);
    try testing.expectEqual(@as(i64, 4000000000), claims.exp);
    try testing.expect(claims.matchesAudience("dashboard-client"));
    try testing.expect(!claims.matchesAudience("lstm-client"));
    try testing.expect(claims.hasRole("dashboard-user"));
    try testing.expect(!claims.hasRole("admin-user"));
}

test "parseClaims: aud as array" {
    const json =
        \\{"iss":"x","exp":1,"aud":["a","b","c"],"realm_access":{"roles":["r1"]}}
    ;
    const claims = try parseClaims(json);
    try testing.expect(claims.matchesAudience("b"));
    try testing.expect(!claims.matchesAudience("d"));
}

test "parseClaims: azp alone satisfies audience" {
    const json =
        \\{"iss":"x","exp":1,"aud":"account","azp":"lstm-client","realm_access":{"roles":["lstm-control"]}}
    ;
    const claims = try parseClaims(json);
    try testing.expect(claims.matchesAudience("lstm-client"));
    try testing.expect(!claims.matchesAudience("account-other"));
    // 'account' is the literal aud, so it also matches by aud:
    try testing.expect(claims.matchesAudience("account"));
}

test "parseClaims: missing realm_access => no roles" {
    const json =
        \\{"iss":"x","exp":1,"aud":"a"}
    ;
    const claims = try parseClaims(json);
    try testing.expect(!claims.hasRole("anything"));
}

test "parseClaims: rejects missing exp" {
    const json =
        \\{"iss":"x","aud":"a"}
    ;
    try testing.expectError(error.MissingExp, parseClaims(json));
}

test "parseClaims: rejects missing iss" {
    const json =
        \\{"exp":1,"aud":"a"}
    ;
    try testing.expectError(error.MissingIss, parseClaims(json));
}

test "stripLeadingZeros: keeps non-zero leader" {
    const out = stripLeadingZeros(&.{ 0, 0, 0xAB, 0xCD });
    try testing.expectEqualSlices(u8, &.{ 0xAB, 0xCD }, out);
}

test "stripLeadingZeros: all zero" {
    const out = stripLeadingZeros(&.{ 0, 0, 0 });
    try testing.expectEqual(@as(usize, 0), out.len);
}

test "Verifier: installKey then findKey" {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", "iss");
    defer v.deinit();
    try v.installKey("kid1", &.{ 0xAB, 0xCD }, &.{ 0x01, 0x00, 0x01 });
    const k = v.findKey("kid1") orelse return error.NotFound;
    try testing.expectEqualStrings("kid1", k.kid);
    try testing.expectEqualSlices(u8, &.{ 0xAB, 0xCD }, k.modulus);
    try testing.expect(v.findKey("other") == null);
}

test "Verifier: copyKey writes into caller buffer" {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", "iss");
    defer v.deinit();
    try v.installKey("kid1", &.{ 0xAB, 0xCD, 0xEF }, &.{ 0x01, 0x00, 0x01 });
    var modulus_buf: [16]u8 = undefined;
    var exponent_buf: [16]u8 = undefined;
    const found = v.copyKey("kid1", &modulus_buf, &exponent_buf) orelse return error.NotFound;
    try testing.expectEqual(@as(usize, 3), found.modulus_len);
    try testing.expectEqual(@as(usize, 3), found.exponent_len);
    try testing.expectEqualSlices(u8, &.{ 0xAB, 0xCD, 0xEF }, modulus_buf[0..found.modulus_len]);
}

test "Verifier: copyKey returns null when buffer too small" {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", "iss");
    defer v.deinit();
    try v.installKey("kid1", &.{ 0xAB, 0xCD, 0xEF, 0x12 }, &.{ 0x01, 0x00, 0x01 });
    var modulus_buf: [2]u8 = undefined;
    var exponent_buf: [16]u8 = undefined;
    try testing.expect(v.copyKey("kid1", &modulus_buf, &exponent_buf) == null);
}

test "parseClaims: aud array followed by realm_roles does not alias" {
    // Regression: aud_slots and role_slots used to share one buffer, so
    // parsing realm_roles after an aud array overwrote claims.aud.multi.
    const json =
        \\{"iss":"x","exp":1,"aud":["controller-client","account"],"realm_access":{"roles":["controller-ingest","offline_access"]}}
    ;
    const claims = try parseClaims(json);
    try testing.expect(claims.matchesAudience("controller-client"));
    try testing.expect(claims.matchesAudience("account"));
    try testing.expect(claims.hasRole("controller-ingest"));
    try testing.expect(claims.hasRole("offline_access"));
    // Critically: audience check must still see the real aud values, not
    // role strings that happened to land in the shared buffer.
    try testing.expect(!claims.matchesAudience("controller-ingest"));
}

// ---------------------------------------------------------------------------
// Full verify() error-path coverage.
//
// Fixtures (RSA-2048 keypair + 10 pre-signed JWTs) come from
// src/auth_test_fixtures.zig, generated offline by scripts/genjwt.py to
// avoid pulling an RSA-signing implementation into the test binary.
// ---------------------------------------------------------------------------

const fixt = @import("auth_test_fixtures.zig");

fn testVerifier() Verifier {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", fixt.test_iss);
    v.installKey(fixt.test_kid, stripLeadingZeros(fixt.test_modulus), fixt.test_exponent) catch unreachable;
    return v;
}

fn verifyToken(v: *Verifier, token: []const u8) VerifyError!void {
    var buf: [4096]u8 = undefined;
    const header = std.fmt.bufPrint(&buf, "Bearer {s}", .{token}) catch unreachable;
    return v.verifyAt(null, header, "expected-aud", "expected-role", fixt.test_now);
}

test "verify: accepts a valid token" {
    var v = testVerifier();
    defer v.deinit();
    try verifyToken(&v, fixt.token_valid);
}

test "verify: accepts a token expired within the skew window" {
    var v = testVerifier();
    defer v.deinit();
    try verifyToken(&v, fixt.token_within_skew);
}

test "verify: rejects a token expired beyond the skew window" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.Expired, verifyToken(&v, fixt.token_beyond_skew));
}

test "verify: rejects a token with the wrong issuer" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.WrongIssuer, verifyToken(&v, fixt.token_wrong_iss));
}

test "verify: rejects a token with the wrong audience" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.WrongAudience, verifyToken(&v, fixt.token_wrong_aud));
}

test "verify: accepts a token whose audience matches via azp fallback" {
    var v = testVerifier();
    defer v.deinit();
    try verifyToken(&v, fixt.token_azp_only);
}

test "verify: rejects a token missing the required role" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.MissingRole, verifyToken(&v, fixt.token_no_role));
}

test "verify: rejects a token signed with an unknown kid (io=null)" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.UnknownKid, verifyToken(&v, fixt.token_unknown_kid));
}

test "verify: rejects a token whose header advertises HS256" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.UnsupportedAlgorithm, verifyToken(&v, fixt.token_hs256_header));
}

test "verify: rejects a tampered signature" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(error.InvalidSignature, verifyToken(&v, fixt.token_bad_sig));
}

test "verify: rejects a malformed Bearer token (non-base64 garbage)" {
    var v = testVerifier();
    defer v.deinit();
    try testing.expectError(
        error.MalformedToken,
        v.verifyAt(null, "Bearer !!!.@@@.###", "a", "r", fixt.test_now),
    );
}

test "verify: rejects a header whose JSON is malformed" {
    var v = testVerifier();
    defer v.deinit();
    // header decodes to "not json" — base64url-encoded
    const bad = "bm90IGpzb24.eyJ4Ijoxfg.AA";
    var buf: [128]u8 = undefined;
    const header = try std.fmt.bufPrint(&buf, "Bearer {s}", .{bad});
    try testing.expectError(error.MalformedToken, v.verifyAt(null, header, "a", "r", fixt.test_now));
}

test "Verifier: rejects token without Bearer prefix" {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", "iss");
    defer v.deinit();
    try testing.expectError(error.MissingBearer, v.verify(null, "not-bearer", "aud", "role"));
}

test "Verifier: malformed token shape" {
    var v = Verifier.init(testing.allocator, "http://nowhere/jwks", "iss");
    defer v.deinit();
    try testing.expectError(error.MalformedToken, v.verify(null, "Bearer onepart", "aud", "role"));
    try testing.expectError(error.MalformedToken, v.verify(null, "Bearer two.parts", "aud", "role"));
}
