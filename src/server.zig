const std = @import("std");
const Io = std.Io;
const net = std.Io.net;
const router = @import("router.zig");
const db = @import("db.zig");

const WorkerArgs = struct {
    io: Io,
    stream: net.Stream,
    allocator: std.mem.Allocator,
    write_connstr: [*:0]const u8,
    read_connstr: [*:0]const u8,
};

pub fn run(
    io: Io,
    allocator: std.mem.Allocator,
    port: u16,
    write_connstr: [*:0]const u8,
    read_connstr: [*:0]const u8,
) !void {
    const address = try net.IpAddress.parse("0.0.0.0", port);
    var listener = try address.listen(io, .{ .reuse_address = true });
    defer listener.deinit(io);

    std.log.info("listening on 0.0.0.0:{d}", .{port});

    while (true) {
        const stream = listener.accept(io) catch |err| {
            std.log.err("accept error: {}", .{err});
            continue;
        };
        const args = WorkerArgs{
            .io = io,
            .stream = stream,
            .allocator = allocator,
            .write_connstr = write_connstr,
            .read_connstr = read_connstr,
        };
        const t = std.Thread.spawn(.{}, connectionWorker, .{args}) catch |err| {
            std.log.err("thread spawn failed: {}", .{err});
            stream.close(io);
            continue;
        };
        t.detach();
    }
}

fn connectionWorker(args: WorkerArgs) void {
    handleConnection(args) catch |err| {
        std.log.err("connection error: {}", .{err});
    };
}

fn handleConnection(args: WorkerArgs) !void {
    const io = args.io;
    const stream = args.stream;
    defer stream.close(io);

    var write_db = try db.Db.connect(args.write_connstr);
    defer write_db.deinit();
    var read_db = try db.Db.connect(args.read_connstr);
    defer read_db.deinit();

    var recv_buf: [8192]u8 = undefined;
    var send_buf: [8192]u8 = undefined;
    var reader = stream.reader(io, &recv_buf);
    var writer = stream.writer(io, &send_buf);

    var http = std.http.Server.init(&reader.interface, &writer.interface);

    while (true) {
        var arena = std.heap.ArenaAllocator.init(args.allocator);
        defer arena.deinit();

        var request = http.receiveHead() catch |err| switch (err) {
            error.HttpConnectionClosing => return,
            else => return err,
        };
        router.dispatch(&request, arena.allocator(), &read_db, &write_db) catch |err| {
            std.log.err("handler error: {}", .{err});
            return err;
        };
    }
}
