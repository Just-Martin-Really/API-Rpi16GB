const std = @import("std");
const db = @import("../db.zig");
const auth = @import("../auth.zig");

const LoginInput = struct {
    username: []const u8,
    password: []const u8,
};

/// POST /auth/login
/// Body: {"username":"...","password":"..."}
/// Returns: {"token":"<jwt>"} or 401
pub fn handle(
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    read_db: *db.Db,
    jwt_secret: []const u8,
) !void {
    var body_buf: [4096]u8 = undefined;
    var read_buf: [4096]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);

    const parsed = std.json.parseFromSlice(LoginInput, allocator, body_buf[0..n], .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    // Hash the submitted password with SHA-256
    var hash: [std.crypto.hash.sha2.Sha256.digest_length]u8 = undefined;
    std.crypto.hash.sha2.Sha256.hash(parsed.value.password, &hash, .{});
    const hex = std.fmt.bytesToHex(hash, .lower);

    // Look up user in DB
    const username_z = try allocator.dupeZ(u8, parsed.value.username);
    const hex_z = try allocator.dupeZ(u8, &hex);
    const result = read_db.queryParams(
        "SELECT id FROM dashboard_users WHERE username = $1 AND password_sha256 = $2",
        &.{ username_z, hex_z },
    ) catch {
        try request.respond("{\"error\":\"unauthorized\"}", .{
            .status = .unauthorized,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };
    defer db.clearResult(result);

    if (db.numRows(result) == 0) {
        try request.respond("{\"error\":\"unauthorized\"}", .{
            .status = .unauthorized,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    }

    // 24-hour token
    const token = try auth.issueToken(allocator, jwt_secret, 86400);
    const body = try std.fmt.allocPrint(allocator, "{{\"token\":\"{s}\"}}", .{token});
    try request.respond(body, .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
