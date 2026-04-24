const std = @import("std");
const server = @import("server.zig");

fn readSecret(path: []const u8, buf: []u8) ![:0]u8 {
    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();
    const n = try file.readAll(buf);
    const trimmed = std.mem.trimRight(u8, buf[0..n], " \n\r");
    buf[trimmed.len] = 0;
    return buf[0..trimmed.len :0];
}

pub fn main() !void {
    var gpa = std.heap.DebugAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var threaded = std.Io.Threaded.init(allocator, .{});
    defer threaded.deinit();
    const io = threaded.io();

    var write_pw_buf: [256]u8 = undefined;
    var read_pw_buf: [256]u8 = undefined;
    const write_pw = try readSecret("/run/secrets/db_write_password", &write_pw_buf);
    const read_pw = try readSecret("/run/secrets/db_read_password", &read_pw_buf);

    var write_connstr_buf: [512]u8 = undefined;
    var read_connstr_buf: [512]u8 = undefined;
    const write_connstr = try std.fmt.bufPrintZ(&write_connstr_buf,
        "host=postgres port=5432 dbname=sensor user=iot_write_user password={s}", .{write_pw});
    const read_connstr = try std.fmt.bufPrintZ(&read_connstr_buf,
        "host=postgres port=5432 dbname=sensor user=iot_read_user password={s}", .{read_pw});

    const port: u16 = 8080;
    std.log.info("starting backend on :{d}", .{port});

    try server.run(io, allocator, port, write_connstr, read_connstr);
}
