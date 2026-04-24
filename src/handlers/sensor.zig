const std = @import("std");

/// GET /api/v1/sensor-data
/// Returns all sensor readings as a JSON array.
/// TODO: query DB with iot_read_user, serialise rows to JSON.
pub fn getAll(request: *std.http.Server.Request) !void {
    try request.respond("[]", .{
        .status = .ok,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "application/json" },
        },
    });
}

/// POST /api/v1/sensor-data
/// Accepts a JSON body and inserts a new sensor reading.
/// TODO: read + validate body, insert via DB with iot_write_user.
pub fn create(request: *std.http.Server.Request) !void {
    // Read body (up to 4 KiB)
    var body_buf: [4096]u8 = undefined;
    var read_buf: [4096]u8 = undefined;
    const reader = request.readerExpectNone(&read_buf);
    const n = try reader.readSliceShort(&body_buf);
    const body = body_buf[0..n];

    // TODO: parse JSON, validate fields, write to sensor_data
    _ = body;

    try request.respond("{\"created\":true}", .{
        .status = .created,
        .extra_headers = &.{
            .{ .name = "content-type", .value = "application/json" },
        },
    });
}
