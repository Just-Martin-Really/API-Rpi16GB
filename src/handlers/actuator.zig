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

/// GET /api/v1/actuator-commands?unsent=true
/// Returns unsent actuator commands as a JSON array. Consumed by controller.py.
pub fn listUnsent(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const result = try db_conn.query(
        "SELECT id, actuator_id, command FROM actuator_commands WHERE sent_at IS NULL ORDER BY issued_at",
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
            "{{\"id\":{s},\"actuator_id\":\"{s}\",\"command\":\"{s}\"}}",
            .{
                db.getValue(result, i, 0),
                db.getValue(result, i, 1),
                db.getValue(result, i, 2),
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

const MarkSentInput = struct {
    id: i64,
};

/// POST /api/v1/actuator-commands/mark-sent
/// Body: {"id": 123}
/// Marks a command as dispatched (sent_at = NOW()).
pub fn markSent(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    var body_buf: [256]u8 = undefined;
    var read_buf: [256]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);

    const parsed = std.json.parseFromSlice(MarkSentInput, allocator, body_buf[0..n], .{}) catch {
        try request.respond("{\"error\":\"invalid json\"}", .{
            .status = .bad_request,
            .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
        });
        return;
    };

    var id_buf: [32]u8 = undefined;
    const id_str = try std.fmt.bufPrintZ(&id_buf, "{d}", .{parsed.value.id});

    try db_conn.execParams(
        "UPDATE actuator_commands SET sent_at = NOW() WHERE id = $1",
        &.{id_str},
    );

    try request.respond("{\"marked\":true}", .{
        .status = .ok,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
