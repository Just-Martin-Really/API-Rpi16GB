const std = @import("std");
const db = @import("db.zig");
const health = @import("handlers/health.zig");
const sensor = @import("handlers/sensor.zig");

pub fn dispatch(
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    read_db: *db.Db,
    write_db: *db.Db,
) !void {
    const target = request.head.target;
    const method = request.head.method;

    if (std.mem.eql(u8, target, "/health")) {
        return health.handle(request);
    }

    if (std.mem.startsWith(u8, target, "/api/v1/sensor-data")) {
        return switch (method) {
            .GET => sensor.getAll(request, allocator, read_db),
            .POST => sensor.create(request, allocator, write_db),
            else => notAllowed(request),
        };
    }

    return notFound(request);
}

fn notFound(request: *std.http.Server.Request) !void {
    try request.respond("404 not found", .{
        .status = .not_found,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "text/plain" },
        },
    });
}

fn notAllowed(request: *std.http.Server.Request) !void {
    try request.respond("405 method not allowed", .{
        .status = .method_not_allowed,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "text/plain" },
        },
    });
}
