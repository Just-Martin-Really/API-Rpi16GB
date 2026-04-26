const std = @import("std");
const db = @import("db.zig");
const auth = @import("auth.zig");
const health = @import("handlers/health.zig");
const sensor = @import("handlers/sensor.zig");
const actuator = @import("handlers/actuator.zig");
const login = @import("handlers/login.zig");

pub fn dispatch(
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    read_db: *db.Db,
    write_db: *db.Db,
    jwt_secret: []const u8,
) !void {
    const target = request.head.target;
    const method = request.head.method;

    if (std.mem.eql(u8, target, "/health")) {
        return health.handle(request);
    }

    if (std.mem.eql(u8, target, "/auth/login") and method == .POST) {
        return login.handle(request, allocator, read_db, jwt_secret);
    }

    if (std.mem.startsWith(u8, target, "/api/")) {
        // Validate JWT
        var header_it = request.iterateHeaders();
        var auth_header: ?[]const u8 = null;
        while (header_it.next()) |h| {
            if (std.ascii.eqlIgnoreCase(h.name, "authorization")) {
                auth_header = h.value;
                break;
            }
        }
        if (auth_header == null) {
            try request.respond("{\"error\":\"missing authorization header\"}", .{
                .status = .unauthorized,
                .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
            });
            return;
        }
        if (!auth.validateBearer(auth_header.?, jwt_secret)) {
            try request.respond("{\"error\":\"invalid or expired token\"}", .{
                .status = .unauthorized,
                .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
            });
            return;
        }

        if (std.mem.startsWith(u8, target, "/api/v1/sensor-data")) {
            return switch (method) {
                .GET => sensor.getAll(request, allocator, read_db),
                .POST => sensor.create(request, allocator, write_db),
                else => notAllowed(request),
            };
        }

        if (std.mem.eql(u8, target, "/api/v1/actuator-command") and method == .POST) {
            return actuator.create(request, allocator, write_db);
        }
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
