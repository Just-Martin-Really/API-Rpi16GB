const std = @import("std");

pub fn handle(request: *std.http.Server.Request) !void {
    try request.respond("{\"status\":\"ok\"}", .{
        .status = .ok,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "application/json" },
        },
    });
}
