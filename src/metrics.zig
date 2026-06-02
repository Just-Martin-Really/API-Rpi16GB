//! Hand-rolled Prometheus exposition for the backend.
//!
//! The Zig stdlib has no metrics library, and pulling a third party in would
//! mean a full HTTP client + JSON dependency tree. The Prometheus text format
//! is small enough to render by hand: a counter is one line per label set, a
//! histogram is one line per bucket plus sum and count. See
//! https://prometheus.io/docs/instrumenting/exposition_formats/ for the spec
//! this module implements.
//!
//! The set of label values is closed: every route the router knows about has
//! a dedicated slot in a fixed-size array, plus an `unknown` slot for paths
//! that hit the 404 path. That keeps the layout immutable and means a stray
//! client can't grow memory by hitting random URLs.
//!
//! Thread safety: the backend spawns one thread per TCP connection (see
//! server.zig). Counters and bucket counts use std.atomic.Value(u64) so
//! observe() and render() never block each other. A render may see a
//! slightly torn snapshot if a request lands mid-scrape (counter
//! incremented, bucket not yet), which is acceptable for monitoring at a
//! 15s scrape interval.

const std = @import("std");

/// Histogram bucket upper bounds in seconds. Matches the Prometheus client
/// library defaults for HTTP latency so Grafana panels stay portable.
pub const buckets = [_]f64{ 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 };

/// All routes the backend can serve. `unknown` covers 404 paths and anything
/// that fails before the router can classify the request. Order is stable;
/// new routes append to the end so existing dashboard queries keep working.
pub const Route = enum {
    health,
    metrics,
    sensor_get,
    sensor_post,
    actuator_post,
    sensor_request_post,
    actuator_commands_get,
    actuator_commands_sent_post,
    sensor_requests_get,
    sensor_requests_sent_post,
    unknown,

    pub fn method(self: Route) []const u8 {
        return switch (self) {
            .health, .metrics, .sensor_get, .actuator_commands_get, .sensor_requests_get => "GET",
            .sensor_post, .actuator_post, .sensor_request_post, .actuator_commands_sent_post, .sensor_requests_sent_post => "POST",
            .unknown => "UNKNOWN",
        };
    }

    pub fn path(self: Route) []const u8 {
        return switch (self) {
            .health => "/health",
            .metrics => "/metrics",
            .sensor_get => "/api/v1/sensor-data",
            .sensor_post => "/api/v1/sensor-data",
            .actuator_post => "/api/v1/actuator-command",
            .sensor_request_post => "/api/v1/sensor-request",
            .actuator_commands_get => "/api/v1/actuator-commands",
            .actuator_commands_sent_post => "/api/v1/actuator-commands/sent",
            .sensor_requests_get => "/api/v1/sensor-requests",
            .sensor_requests_sent_post => "/api/v1/sensor-requests/sent",
            .unknown => "unknown",
        };
    }
};

const route_count = @typeInfo(Route).@"enum".fields.len;

const AtomicU64 = std.atomic.Value(u64);

const Stats = struct {
    count: AtomicU64 = AtomicU64.init(0),
    duration_sum_ns: AtomicU64 = AtomicU64.init(0),
    bucket_counts: [buckets.len]AtomicU64 = blk: {
        var arr: [buckets.len]AtomicU64 = undefined;
        for (&arr) |*slot| slot.* = AtomicU64.init(0);
        break :blk arr;
    },
};

pub const Registry = struct {
    stats: [route_count]Stats = blk: {
        var arr: [route_count]Stats = undefined;
        for (&arr) |*slot| slot.* = .{};
        break :blk arr;
    },
    start_unix_seconds: i64,

    pub fn init(io: std.Io) Registry {
        return .{
            .start_unix_seconds = std.Io.Clock.now(.real, io).toSeconds(),
        };
    }

    /// Record one finished request. `duration_ns` is the time between
    /// receiving the request head and returning from the handler.
    pub fn observe(self: *Registry, route: Route, duration_ns: u64) void {
        const idx = @intFromEnum(route);
        var s = &self.stats[idx];
        _ = s.count.fetchAdd(1, .monotonic);
        _ = s.duration_sum_ns.fetchAdd(duration_ns, .monotonic);
        const duration_s = @as(f64, @floatFromInt(duration_ns)) / std.time.ns_per_s;
        inline for (buckets, 0..) |bound, i| {
            if (duration_s <= bound) _ = s.bucket_counts[i].fetchAdd(1, .monotonic);
        }
    }

    /// Append the full Prometheus text exposition to `buf`.
    /// Allocations for formatted lines come from `allocator`; the caller
    /// owns `buf`.
    pub fn render(
        self: *Registry,
        allocator: std.mem.Allocator,
        buf: *std.ArrayList(u8),
    ) !void {
        try buf.appendSlice(allocator,
            \\# HELP backend_http_requests_total Total HTTP requests handled by the backend.
            \\# TYPE backend_http_requests_total counter
            \\
        );
        for (&self.stats, 0..) |*s, i| {
            const route: Route = @enumFromInt(i);
            const count = s.count.load(.monotonic);
            const line = try std.fmt.allocPrint(
                allocator,
                "backend_http_requests_total{{method=\"{s}\",route=\"{s}\"}} {d}\n",
                .{ route.method(), route.path(), count },
            );
            defer allocator.free(line);
            try buf.appendSlice(allocator, line);
        }

        try buf.appendSlice(allocator,
            \\# HELP backend_http_request_duration_seconds Wall-clock request duration in seconds.
            \\# TYPE backend_http_request_duration_seconds histogram
            \\
        );
        for (&self.stats, 0..) |*s, i| {
            const route: Route = @enumFromInt(i);
            const count = s.count.load(.monotonic);
            for (buckets, &s.bucket_counts) |bound, *bc| {
                const bucket_count = bc.load(.monotonic);
                const line = try std.fmt.allocPrint(
                    allocator,
                    "backend_http_request_duration_seconds_bucket{{method=\"{s}\",route=\"{s}\",le=\"{d}\"}} {d}\n",
                    .{ route.method(), route.path(), bound, bucket_count },
                );
                defer allocator.free(line);
                try buf.appendSlice(allocator, line);
            }
            const inf_line = try std.fmt.allocPrint(
                allocator,
                "backend_http_request_duration_seconds_bucket{{method=\"{s}\",route=\"{s}\",le=\"+Inf\"}} {d}\n",
                .{ route.method(), route.path(), count },
            );
            defer allocator.free(inf_line);
            try buf.appendSlice(allocator, inf_line);

            const sum_ns = s.duration_sum_ns.load(.monotonic);
            const sum_s = @as(f64, @floatFromInt(sum_ns)) / std.time.ns_per_s;
            const sum_line = try std.fmt.allocPrint(
                allocator,
                "backend_http_request_duration_seconds_sum{{method=\"{s}\",route=\"{s}\"}} {d}\n",
                .{ route.method(), route.path(), sum_s },
            );
            defer allocator.free(sum_line);
            try buf.appendSlice(allocator, sum_line);

            const count_line = try std.fmt.allocPrint(
                allocator,
                "backend_http_request_duration_seconds_count{{method=\"{s}\",route=\"{s}\"}} {d}\n",
                .{ route.method(), route.path(), count },
            );
            defer allocator.free(count_line);
            try buf.appendSlice(allocator, count_line);
        }

        try buf.appendSlice(allocator,
            \\# HELP backend_process_start_time_seconds Unix timestamp of process start.
            \\# TYPE backend_process_start_time_seconds gauge
            \\
        );
        const start_line = try std.fmt.allocPrint(
            allocator,
            "backend_process_start_time_seconds {d}\n",
            .{self.start_unix_seconds},
        );
        defer allocator.free(start_line);
        try buf.appendSlice(allocator, start_line);
    }
};
