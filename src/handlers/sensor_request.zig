const std = @import("std");
const db = @import("../db.zig");

const SensorRequestInput = struct {
    sensor_id: []const u8,
    command: []const u8,
};

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
