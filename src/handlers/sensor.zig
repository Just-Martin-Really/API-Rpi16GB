const std = @import("std");
const db = @import("../db.zig");

/// GET /api/v1/sensor-data
/// Returns all sensor readings as a JSON array, newest first.
pub fn getAll(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const result = try db_conn.query(
        "SELECT id, sensor_id, value, unit, recorded_at FROM sensor_data ORDER BY recorded_at DESC",
    );
    defer db.clearResult(result);

    const buf = try allocator.alloc(u8, 65536);
    var pos: usize = 0;

    buf[pos] = '[';
    pos += 1;

    const nrows = db.numRows(result);
    for (0..nrows) |i| {
        if (i > 0) {
            buf[pos] = ',';
            pos += 1;
        }
        const written = try std.fmt.bufPrint(buf[pos..],
            "{{\"id\":{s},\"sensor_id\":\"{s}\",\"value\":{s},\"unit\":\"{s}\",\"recorded_at\":\"{s}\"}}",
            .{
                db.getValue(result, i, 0),
                db.getValue(result, i, 1),
                db.getValue(result, i, 2),
                db.getValue(result, i, 3),
                db.getValue(result, i, 4),
            },
        );
        pos += written.len;
    }

    buf[pos] = ']';
    pos += 1;

    try request.respond(buf[0..pos], .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
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
