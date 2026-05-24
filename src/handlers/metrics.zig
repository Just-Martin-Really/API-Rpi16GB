const std = @import("std");
const metrics = @import("../metrics.zig");

/// GET /metrics
/// Renders the Prometheus text exposition for this backend instance.
/// Unauthenticated by design: Prometheus scrapes from the same Docker
/// network and has no JWT client. The route is reachable only from
/// app-net, never via nginx.
pub fn handle(
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    registry: *metrics.Registry,
) !void {
    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);

    try registry.render(allocator, &buf);

    try request.respond(buf.items, .{
        .status = .ok,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "text/plain; version=0.0.4; charset=utf-8" },
        },
    });
}
