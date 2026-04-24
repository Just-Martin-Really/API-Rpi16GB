const std = @import("std");
const Io = std.Io;
const net = std.Io.net;
const router = @import("router.zig");

pub fn run(io: Io, port: u16) !void {
    const address = try net.IpAddress.parse("0.0.0.0", port);
    var listener = try address.listen(io, .{ .reuse_address = true });
    defer listener.deinit(io);

    std.log.info("listening on 0.0.0.0:{d}", .{port});

    while (true) {
        const stream = listener.accept(io) catch |err| {
            std.log.err("accept error: {}", .{err});
            continue;
        };
        const t = std.Thread.spawn(.{}, connectionWorker, .{ io, stream }) catch |err| {
            std.log.err("thread spawn failed: {}", .{err});
            stream.close(io);
            continue;
        };
        t.detach();
    }
}

fn connectionWorker(io: Io, stream: net.Stream) void {
    handleConnection(io, stream) catch |err| {
        std.log.err("connection error: {}", .{err});
    };
}

fn handleConnection(io: Io, stream: net.Stream) !void {
    defer stream.close(io);

    var recv_buf: [8192]u8 = undefined;
    var send_buf: [8192]u8 = undefined;
    var reader = stream.reader(io, &recv_buf);
    var writer = stream.writer(io, &send_buf);

    var http = std.http.Server.init(&reader.interface, &writer.interface);

    while (true) {
        var request = http.receiveHead() catch |err| switch (err) {
            error.HttpConnectionClosing => return,
            else => return err,
        };
        router.dispatch(&request) catch |err| {
            std.log.err("handler error: {}", .{err});
            return err;
        };
    }
}
