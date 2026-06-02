const std = @import("std");
const db = @import("../db.zig");

const SensorRequestInput = struct {
    sensor_id: []const u8,
    command: []const u8,
};

const SentInput = struct {
    id: i64,
};

/// GET /api/v1/sensor-requests
/// Returns up to 100 unsent rows oldest-first: {"requests":[{"id":N,"sensor_id":"...","command":"..."}, ...]}
/// controller.py polls this every 2s and publishes each row to MQTT before
/// acking via /sent.
pub fn listOpen(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const result = try db_conn.query(
        "SELECT id, sensor_id, command FROM sensor_requests WHERE sent_at IS NULL ORDER BY issued_at LIMIT 100",
    );
    defer db.clearResult(result);

    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);

    try buf.appendSlice(allocator, "{\"requests\":[");
    const nrows = db.numRows(result);
    for (0..nrows) |i| {
        if (i > 0) try buf.append(allocator, ',');
        const row = try std.fmt.allocPrint(allocator, "{{\"id\":{s},\"sensor_id\":\"{s}\",\"command\":\"{s}\"}}", .{
            db.getValue(result, i, 0),
            db.getValue(result, i, 1),
            db.getValue(result, i, 2),
        });
        defer allocator.free(row);
        try buf.appendSlice(allocator, row);
    }
    try buf.appendSlice(allocator, "]}");

    try request.respond(buf.items, .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}

/// POST /api/v1/sensor-requests/sent
/// Body: {"id": <positive integer>}
/// Idempotently marks the row as sent (sent_at = NOW()). Returns
/// {"updated": N} where N is 0 or 1.
pub fn markSent(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    var body_buf: [256]u8 = undefined;
    var read_buf: [256]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);

    const parsed = std.json.parseFromSlice(SentInput, allocator, body_buf[0..n], .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    if (parsed.value.id <= 0) {
        try request.respond("{\"error\":\"id must be a positive integer\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    }

    var id_buf: [32]u8 = undefined;
    const id_z = try std.fmt.bufPrintZ(&id_buf, "{d}", .{parsed.value.id});

    const result = try db_conn.queryParams(
        "UPDATE sensor_requests SET sent_at = NOW() WHERE id = $1 AND sent_at IS NULL RETURNING id",
        &.{id_z},
    );
    defer db.clearResult(result);

    const updated = db.numRows(result);
    const body = try std.fmt.allocPrint(allocator, "{{\"updated\":{d}}}", .{updated});
    defer allocator.free(body);

    try request.respond(body, .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}

/// POST /api/v1/sensor-request
/// Body: {"sensor_id":"sensor01","command":"READ_NOW"}
/// Inserts a row into sensor_requests; controller.py drains and publishes it
/// to "<sensor_id>/request" on MQTT.
pub fn create(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    var body_buf: [4096]u8 = undefined;
    var read_buf: [4096]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);

    const parsed = std.json.parseFromSlice(SensorRequestInput, allocator, body_buf[0..n], .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    const sensor_id_z = try allocator.dupeZ(u8, parsed.value.sensor_id);
    const command_z = try allocator.dupeZ(u8, parsed.value.command);

    try db_conn.execParams(
        "INSERT INTO sensor_requests (sensor_id, command) VALUES ($1, $2)",
        &.{ sensor_id_z, command_z },
    );

    try request.respond("{\"queued\":true}", .{
        .status = .created,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
