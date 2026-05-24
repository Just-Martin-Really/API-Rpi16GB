const std = @import("std");
const db = @import("db.zig");
const auth = @import("auth.zig");
const metrics = @import("metrics.zig");
const health = @import("handlers/health.zig");
const metrics_handler = @import("handlers/metrics.zig");
const sensor = @import("handlers/sensor.zig");
const actuator = @import("handlers/actuator.zig");
const sensor_request = @import("handlers/sensor_request.zig");

/// Per-route audience + role policy.
const RoutePolicy = struct {
    audience: []const u8,
    role: []const u8,
};

pub fn dispatch(
    io: std.Io,
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    read_db: *db.Db,
    write_db: *db.Db,
    verifier: *auth.Verifier,
    registry: *metrics.Registry,
) !void {
    const target = request.head.target;
    const method = request.head.method;

    if (std.mem.eql(u8, target, "/health")) {
        return health.handle(request);
    }

    // /metrics is unauthenticated. Prometheus scrapes from inside app-net and
    // has no JWT client; nginx never proxies this path.
    if (std.mem.eql(u8, target, "/metrics") and method == .GET) {
        return metrics_handler.handle(request, allocator, registry);
    }

    if (!std.mem.startsWith(u8, target, "/api/")) {
        return notFound(request);
    }

    const path = stripQuery(target);

    // Resolve route + policy first so an unknown route does not require auth
    // and a method-not-allowed answer is consistent across auth states.
    const route = resolveRoute(path, method) orelse return notFound(request);

    // AuthN/AuthZ.
    var header_it = request.iterateHeaders();
    var auth_header: ?[]const u8 = null;
    while (header_it.next()) |h| {
        if (std.ascii.eqlIgnoreCase(h.name, "authorization")) {
            auth_header = h.value;
            break;
        }
    }
    if (auth_header == null) {
        return respondJson(request, .unauthorized, "{\"error\":\"missing authorization header\"}");
    }
    verifier.verify(io, auth_header.?, route.policy.audience, route.policy.role) catch |err| {
        std.log.warn("auth rejected on {s}: {s}", .{ path, @errorName(err) });
        const status: std.http.Status = switch (err) {
            error.MissingBearer => .unauthorized,
            else => .forbidden,
        };
        return respondJson(request, status, "{\"error\":\"forbidden\"}");
    };

    return switch (route.kind) {
        .sensor_get => sensor.getAll(request, allocator, read_db),
        .sensor_post => sensor.create(request, allocator, write_db),
        .actuator_post => actuator.create(request, allocator, write_db),
        .sensor_request_post => sensor_request.create(request, allocator, write_db),
    };
}

const RouteKind = enum {
    sensor_get,
    sensor_post,
    actuator_post,
    sensor_request_post,
};

const Route = struct {
    kind: RouteKind,
    policy: RoutePolicy,
};

const policy_dashboard: RoutePolicy = .{ .audience = "dashboard-client", .role = "dashboard-user" };
const policy_controller: RoutePolicy = .{ .audience = "controller-client", .role = "controller-ingest" };
const policy_lstm: RoutePolicy = .{ .audience = "lstm-client", .role = "lstm-control" };

fn resolveRoute(path: []const u8, method: std.http.Method) ?Route {
    if (std.mem.eql(u8, path, "/api/v1/sensor-data")) {
        return switch (method) {
            .GET => .{ .kind = .sensor_get, .policy = policy_dashboard },
            .POST => .{ .kind = .sensor_post, .policy = policy_controller },
            else => null,
        };
    }
    if (std.mem.eql(u8, path, "/api/v1/actuator-command") and method == .POST) {
        return .{ .kind = .actuator_post, .policy = policy_lstm };
    }
    if (std.mem.eql(u8, path, "/api/v1/sensor-request") and method == .POST) {
        return .{ .kind = .sensor_request_post, .policy = policy_dashboard };
    }
    return null;
}

fn stripQuery(target: []const u8) []const u8 {
    const idx = std.mem.indexOfScalar(u8, target, '?') orelse return target;
    return target[0..idx];
}

/// Classify a request to a metrics label without invoking the handler.
/// Called by server.zig before dispatch so that timing still works when the
/// handler errors out. Unknown paths and method-not-allowed cases fall
/// through to `.unknown`.
pub fn routeFor(target: []const u8, method: std.http.Method) metrics.Route {
    const path = stripQuery(target);
    if (std.mem.eql(u8, path, "/health")) return .health;
    if (std.mem.eql(u8, path, "/metrics") and method == .GET) return .metrics;
    const route = resolveRoute(path, method) orelse return .unknown;
    return switch (route.kind) {
        .sensor_get => .sensor_get,
        .sensor_post => .sensor_post,
        .actuator_post => .actuator_post,
        .sensor_request_post => .sensor_request_post,
    };
}

fn respondJson(request: *std.http.Server.Request, status: std.http.Status, body: []const u8) !void {
    try request.respond(body, .{
        .status = status,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}

fn notFound(request: *std.http.Server.Request) !void {
    try request.respond("404 not found", .{
        .status = .not_found,
        .extra_headers = &.{.{ .name = "content-type", .value = "text/plain" }},
    });
}
