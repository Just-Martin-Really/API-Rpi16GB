const std = @import("std");
const db = @import("../db.zig");

const ActuatorInput = struct {
    actuator_id: []const u8,
    command: []const u8,
};

/// POST /api/v1/actuator-command
/// Body: {"actuator_id":"actuator01","command":"on"}
/// Inserts a command row; controller.py picks it up and publishes to MQTT.
pub fn create(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    var body_buf: [4096]u8 = undefined;
    var read_buf: [4096]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);

    const parsed = std.json.parseFromSlice(ActuatorInput, allocator, body_buf[0..n], .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    const actuator_id_z = try allocator.dupeZ(u8, parsed.value.actuator_id);
    const command_z = try allocator.dupeZ(u8, parsed.value.command);

    try db_conn.execParams(
        "INSERT INTO actuator_commands (actuator_id, command) VALUES ($1, $2)",
        &.{ actuator_id_z, command_z },
    );

    try request.respond("{\"queued\":true}", .{
        .status = .created,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
