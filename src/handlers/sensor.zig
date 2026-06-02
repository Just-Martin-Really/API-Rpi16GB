const std = @import("std");
const db = @import("../db.zig");

/// GET /api/v1/sensor-data
/// GET /api/v1/sensor-data?from=<iso8601>&to=<iso8601>
/// Returns sensor readings as a JSON array, newest first.
/// `from` / `to` are optional ISO-8601 timestamps; either or both may be set.
pub fn getAll(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const target = request.head.target;
    const range = parseRange(allocator, target) catch |err| switch (err) {
        error.BadRange => {
            try request.respond("{\"error\":\"invalid from/to\"}", .{
                .status = .bad_request,
                .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
            });
            return;
        },
        else => return err,
    };
    defer range.deinit(allocator);

    const result = try fetchRows(allocator, db_conn, range);
    defer db.clearResult(result);

    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);

    try buf.append(allocator, '[');

    const nrows = db.numRows(result);
    for (0..nrows) |i| {
        if (i > 0) try buf.append(allocator, ',');
        const row = try std.fmt.allocPrint(allocator, "{{\"id\":{s},\"sensor_id\":\"{s}\",\"value\":{s},\"unit\":\"{s}\",\"recorded_at\":\"{s}\"}}", .{
            db.getValue(result, i, 0),
            db.getValue(result, i, 1),
            db.getValue(result, i, 2),
            db.getValue(result, i, 3),
            db.getValue(result, i, 4),
        });
        defer allocator.free(row);
        try buf.appendSlice(allocator, row);
    }

    try buf.append(allocator, ']');

    try request.respond(buf.items, .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}

const Range = struct {
    from: ?[:0]const u8 = null,
    to: ?[:0]const u8 = null,

    fn deinit(self: Range, allocator: std.mem.Allocator) void {
        if (self.from) |s| allocator.free(s);
        if (self.to) |s| allocator.free(s);
    }
};

fn parseRange(allocator: std.mem.Allocator, target: []const u8) !Range {
    const qmark = std.mem.indexOfScalar(u8, target, '?') orelse return Range{};
    var range = Range{};
    errdefer range.deinit(allocator);

    var it = std.mem.splitScalar(u8, target[qmark + 1 ..], '&');
    while (it.next()) |pair| {
        const eq = std.mem.indexOfScalar(u8, pair, '=') orelse continue;
        const key = pair[0..eq];
        const value = pair[eq + 1 ..];
        if (std.mem.eql(u8, key, "from")) {
            range.from = try percentDecodeZ(allocator, value);
        } else if (std.mem.eql(u8, key, "to")) {
            range.to = try percentDecodeZ(allocator, value);
        }
    }

    // Reject obviously empty values; Postgres will reject malformed ones via
    // the query path with a 500, which is acceptable for a malformed client.
    if (range.from) |s| if (s.len == 0) return error.BadRange;
    if (range.to) |s| if (s.len == 0) return error.BadRange;

    return range;
}

fn percentDecodeZ(allocator: std.mem.Allocator, src: []const u8) ![:0]const u8 {
    var buf = try allocator.alloc(u8, src.len + 1);
    var n: usize = 0;
    var i: usize = 0;
    while (i < src.len) {
        const ch = src[i];
        if (ch == '%' and i + 2 < src.len) {
            const hi = hexNibble(src[i + 1]) orelse return error.BadRange;
            const lo = hexNibble(src[i + 2]) orelse return error.BadRange;
            buf[n] = (hi << 4) | lo;
            i += 3;
        } else if (ch == '+') {
            buf[n] = ' ';
            i += 1;
        } else {
            buf[n] = ch;
            i += 1;
        }
        n += 1;
    }
    buf[n] = 0;
    return buf[0..n :0];
}

fn hexNibble(b: u8) ?u8 {
    return switch (b) {
        '0'...'9' => b - '0',
        'a'...'f' => b - 'a' + 10,
        'A'...'F' => b - 'A' + 10,
        else => null,
    };
}

fn fetchRows(allocator: std.mem.Allocator, db_conn: *db.Db, range: Range) !*db.c.PGresult {
    if (range.from != null and range.to != null) {
        return db_conn.queryParams(
            "SELECT id, sensor_id, value, unit, recorded_at FROM sensor_data WHERE recorded_at >= $1 AND recorded_at <= $2 ORDER BY recorded_at DESC",
            &.{ range.from.?, range.to.? },
        );
    }
    if (range.from) |from| {
        return db_conn.queryParams(
            "SELECT id, sensor_id, value, unit, recorded_at FROM sensor_data WHERE recorded_at >= $1 ORDER BY recorded_at DESC",
            &.{from},
        );
    }
    if (range.to) |to| {
        return db_conn.queryParams(
            "SELECT id, sensor_id, value, unit, recorded_at FROM sensor_data WHERE recorded_at <= $1 ORDER BY recorded_at DESC",
            &.{to},
        );
    }
    _ = allocator;
    return db_conn.query(
        "SELECT id, sensor_id, value, unit, recorded_at FROM sensor_data ORDER BY recorded_at DESC",
    );
}

const SensorInput = struct {
    sensor_id: []const u8,
    value: f64,
    unit: []const u8,
};

/// POST /api/v1/sensor-data
/// Body: {"sensor_id":"<id>","value":<number>,"unit":"<unit>"}
pub fn create(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    var body_buf: [4096]u8 = undefined;
    var read_buf: [4096]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);
    const body = body_buf[0..n];

    const parsed = std.json.parseFromSlice(SensorInput, allocator, body, .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    const sensor_id_z = try allocator.dupeZ(u8, parsed.value.sensor_id);
    const unit_z = try allocator.dupeZ(u8, parsed.value.unit);
    var value_buf: [64]u8 = undefined;
    const value_str = try std.fmt.bufPrintZ(&value_buf, "{d}", .{parsed.value.value});

    try db_conn.execParams(
        "INSERT INTO sensor_data (sensor_id, value, unit) VALUES ($1, $2, $3)",
        &.{ sensor_id_z, value_str, unit_z },
    );

    try request.respond("{\"created\":true}", .{
        .status = .created,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
