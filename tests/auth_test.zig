const std = @import("std");
const auth = @import("auth");

test "issueToken: produces 3-part JWT" {
    var buf: [512]u8 = undefined;
    var fba = std.heap.FixedBufferAllocator.init(&buf);
    const token = try auth.issueToken(fba.allocator(), "testsecret", 3600);
    var dot_count: usize = 0;
    for (token) |ch| if (ch == '.') {
        dot_count += 1;
    };
    try std.testing.expectEqual(@as(usize, 2), dot_count);
}

test "validateBearer: accepts valid token" {
    var buf: [512]u8 = undefined;
    var fba = std.heap.FixedBufferAllocator.init(&buf);
    const token = try auth.issueToken(fba.allocator(), "testsecret", 3600);
    var bearer_buf: [600]u8 = undefined;
    const bearer = try std.fmt.bufPrint(&bearer_buf, "Bearer {s}", .{token});
    try std.testing.expect(auth.validateBearer(bearer, "testsecret"));
}

test "validateBearer: rejects wrong secret" {
    var buf: [512]u8 = undefined;
    var fba = std.heap.FixedBufferAllocator.init(&buf);
    const token = try auth.issueToken(fba.allocator(), "testsecret", 3600);
    var bearer_buf: [600]u8 = undefined;
    const bearer = try std.fmt.bufPrint(&bearer_buf, "Bearer {s}", .{token});
    try std.testing.expect(!auth.validateBearer(bearer, "wrongsecret"));
}

test "validateBearer: rejects expired token" {
    var buf: [512]u8 = undefined;
    var fba = std.heap.FixedBufferAllocator.init(&buf);
    const token = try auth.issueToken(fba.allocator(), "testsecret", -1);
    var bearer_buf: [600]u8 = undefined;
    const bearer = try std.fmt.bufPrint(&bearer_buf, "Bearer {s}", .{token});
    try std.testing.expect(!auth.validateBearer(bearer, "testsecret"));
}

test "validateBearer: rejects missing Bearer prefix" {
    try std.testing.expect(!auth.validateBearer("somejwt.without.prefix", "secret"));
}

test "validateBearer: rejects single-part token" {
    try std.testing.expect(!auth.validateBearer("Bearer onlyone", "secret"));
}

test "validateBearer: rejects tampered payload" {
    var buf: [512]u8 = undefined;
    var fba = std.heap.FixedBufferAllocator.init(&buf);
    const token = try auth.issueToken(fba.allocator(), "testsecret", 3600);
    const dot1 = std.mem.indexOfScalar(u8, token, '.').?;
    token[dot1 + 1] ^= 0x01;
    var bearer_buf: [600]u8 = undefined;
    const bearer = try std.fmt.bufPrint(&bearer_buf, "Bearer {s}", .{token});
    try std.testing.expect(!auth.validateBearer(bearer, "testsecret"));
}
