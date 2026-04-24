const std = @import("std");
const server = @import("server.zig");

pub fn main() !void {
    var gpa = std.heap.DebugAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var threaded = std.Io.Threaded.init(allocator, .{});
    defer threaded.deinit();
    const io = threaded.io();

    const port: u16 = 8080;
    std.log.info("starting backend on :{d}", .{port});

    try server.run(io, port);
}
