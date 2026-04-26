const std = @import("std");
const c = @cImport(@cInclude("time.h"));

const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;

// base64url no-pad alphabet (RFC 4648 §5)
const b64_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
const b64enc = std.base64.Base64Encoder.init(b64_alphabet.*, null);
const b64dec = std.base64.Base64Decoder.init(b64_alphabet.*, null);

// Pre-computed base64url of {"alg":"HS256","typ":"JWT"}
const header_b64 = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9";

pub fn issueToken(allocator: std.mem.Allocator, secret: []const u8, ttl_seconds: i64) ![]u8 {
    const exp = @as(i64, c.time(null)) + ttl_seconds;

    var payload_json_buf: [128]u8 = undefined;
    const payload_json = try std.fmt.bufPrint(
        &payload_json_buf,
        "{{\"sub\":\"dashboard\",\"exp\":{d}}}",
        .{exp},
    );

    var payload_b64_buf: [256]u8 = undefined;
    const payload_b64_len = b64enc.calcSize(payload_json.len);
    const payload_b64 = b64enc.encode(payload_b64_buf[0..payload_b64_len], payload_json);

    var signing_buf: [512]u8 = undefined;
    const signing_input = try std.fmt.bufPrint(
        &signing_buf,
        "{s}.{s}",
        .{ header_b64, payload_b64 },
    );

    var mac: [Hmac.mac_length]u8 = undefined;
    Hmac.create(&mac, signing_input, secret);

    var sig_buf: [64]u8 = undefined;
    const sig_len = b64enc.calcSize(mac.len);
    const sig = b64enc.encode(sig_buf[0..sig_len], &mac);

    return std.fmt.allocPrint(allocator, "{s}.{s}.{s}", .{ header_b64, payload_b64, sig });
}

/// Returns true if Authorization header value is a valid, unexpired Bearer token.
pub fn validateBearer(authorization: []const u8, secret: []const u8) bool {
    const prefix = "Bearer ";
    if (!std.mem.startsWith(u8, authorization, prefix)) return false;
    return validateToken(authorization[prefix.len..], secret);
}

fn validateToken(token: []const u8, secret: []const u8) bool {
    const dot1 = std.mem.indexOfScalar(u8, token, '.') orelse return false;
    const after1 = token[dot1 + 1 ..];
    const dot2 = std.mem.indexOfScalar(u8, after1, '.') orelse return false;

    // signing_input = "header_b64.payload_b64"
    const signing_input = token[0 .. dot1 + 1 + dot2];
    const sig_b64 = after1[dot2 + 1 ..];

    // Decode signature
    var decoded_sig: [Hmac.mac_length]u8 = undefined;
    const decoded_sig_len = b64dec.calcSizeForSlice(sig_b64) catch return false;
    if (decoded_sig_len != Hmac.mac_length) return false;
    b64dec.decode(&decoded_sig, sig_b64) catch return false;

    // Recompute and compare (XOR accumulate — constant-time enough for this use case)
    var expected: [Hmac.mac_length]u8 = undefined;
    Hmac.create(&expected, signing_input, secret);
    var acc: u8 = 0;
    for (decoded_sig, expected) |a, b| acc |= a ^ b;
    if (acc != 0) return false;

    // Decode payload and check exp
    const payload_b64 = token[dot1 + 1 .. dot1 + 1 + dot2];
    var payload_buf: [256]u8 = undefined;
    const payload_len = b64dec.calcSizeForSlice(payload_b64) catch return false;
    if (payload_len > payload_buf.len) return false;
    b64dec.decode(payload_buf[0..payload_len], payload_b64) catch return false;
    const payload = payload_buf[0..payload_len];

    // Extract exp field
    const exp_key = "\"exp\":";
    const exp_start_idx = std.mem.indexOf(u8, payload, exp_key) orelse return false;
    const digits_start = exp_start_idx + exp_key.len;
    var digits_end = digits_start;
    while (digits_end < payload.len and payload[digits_end] >= '0' and payload[digits_end] <= '9') {
        digits_end += 1;
    }
    const exp = std.fmt.parseInt(i64, payload[digits_start..digits_end], 10) catch return false;

    return @as(i64, c.time(null)) < exp;
}
