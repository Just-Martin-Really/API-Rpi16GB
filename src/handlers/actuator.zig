const std = @import("std");
const db = @import("../db.zig");

const ActuatorInput = struct {
    actuator_id: []const u8,
    command: []const u8,
    issued_by: ?[]const u8 = null,
};

const SentInput = struct {
    id: i64,
};

/// GET /api/v1/actuator-states
/// Returns the latest sent command per actuator_id so the dashboard can
/// render the current on/off state of each relay. Rows where sent_at IS NULL
/// are excluded — those are queued but not yet on the wire.
/// Shape: {"actuators":[{"actuator_id":"...","command":"...","sent_at":"..."}, ...]}
pub fn listStates(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const result = try db_conn.query(
        "SELECT DISTINCT ON (actuator_id) actuator_id, command, sent_at " ++
            "FROM actuator_commands " ++
            "WHERE sent_at IS NOT NULL " ++
            "ORDER BY actuator_id, sent_at DESC",
    );
    defer db.clearResult(result);

    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);

    try buf.appendSlice(allocator, "{\"actuators\":[");
    const nrows = db.numRows(result);
    for (0..nrows) |i| {
        if (i > 0) try buf.append(allocator, ',');
        const row = try std.fmt.allocPrint(allocator, "{{\"actuator_id\":\"{s}\",\"command\":\"{s}\",\"sent_at\":\"{s}\"}}", .{
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

/// GET /api/v1/actuator-commands
/// Returns up to 100 unsent rows oldest-first: {"commands":[{"id":N,"actuator_id":"...","command":"..."}, ...]}
/// controller.py polls this every 2s and publishes each row to MQTT before
/// acking via /sent.
pub fn listOpen(request: *std.http.Server.Request, allocator: std.mem.Allocator, db_conn: *db.Db) !void {
    const result = try db_conn.query(
        "SELECT id, actuator_id, command FROM actuator_commands WHERE sent_at IS NULL ORDER BY issued_at LIMIT 100",
    );
    defer db.clearResult(result);

    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);

    try buf.appendSlice(allocator, "{\"commands\":[");
    const nrows = db.numRows(result);
    for (0..nrows) |i| {
        if (i > 0) try buf.append(allocator, ',');
        const row = try std.fmt.allocPrint(allocator, "{{\"id\":{s},\"actuator_id\":\"{s}\",\"command\":\"{s}\"}}", .{
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

/// POST /api/v1/actuator-commands/sent
/// Body: {"id": <positive integer>}
/// Idempotently marks the row as sent (sent_at = NOW()). Returns
/// {"updated": N} where N is 0 or 1 — already-sent rows return 0.
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
        "UPDATE actuator_commands SET sent_at = NOW() WHERE id = $1 AND sent_at IS NULL RETURNING id",
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

/// POST /api/v1/actuator-command
/// Body: {"actuator_id":"actuator01","command":"on"}
/// Inserts a command row; controller.py picks it up and publishes to MQTT.
///
/// issued_by is derived from the verified audience, not the request body, so
/// a dashboard token cannot pollute the audit trail by claiming "machine"
/// origin. dashboard-client → "user", lstm-client → "machine".
pub fn create(
    request: *std.http.Server.Request,
    allocator: std.mem.Allocator,
    db_conn: *db.Db,
    matched_audience: []const u8,
) !void {
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

    const issued_by: []const u8 = if (std.mem.eql(u8, matched_audience, "lstm-client"))
        "machine"
    else
        "user";

    const actuator_id_z = try allocator.dupeZ(u8, parsed.value.actuator_id);
    const command_z = try allocator.dupeZ(u8, parsed.value.command);
    const issued_by_z = try allocator.dupeZ(u8, issued_by);

    try db_conn.execParams(
        "INSERT INTO actuator_commands (actuator_id, command, issued_by) VALUES ($1, $2, $3)",
        &.{ actuator_id_z, command_z, issued_by_z },
    );

    try request.respond("{\"queued\":true}", .{
        .status = .created,
        .extra_headers = &.{.{ .name = "content-type", .value = "application/json" }},
    });
}
