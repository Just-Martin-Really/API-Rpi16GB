const std = @import("std");
const c = @cImport({
    @cInclude("libpq-fe.h");
});

pub const Db = struct {
    conn: *c.PGconn,

    pub fn connect(connstr: [*:0]const u8) !Db {
        const conn = c.PQconnectdb(connstr) orelse return error.OutOfMemory;
        if (c.PQstatus(conn) != c.CONNECTION_OK) {
            std.log.err("DB connection failed: {s}", .{c.PQerrorMessage(conn)});
            c.PQfinish(conn);
            return error.DbConnectionFailed;
        }
        std.log.info("DB connection established", .{});
        return .{ .conn = conn };
    }

    pub fn deinit(self: *Db) void {
        c.PQfinish(self.conn);
    }

    /// Execute a query that returns rows (SELECT).
    pub fn query(self: *Db, sql: [*:0]const u8) !*c.PGresult {
        const result = c.PQexec(self.conn, sql) orelse return error.OutOfMemory;
        if (c.PQresultStatus(result) != c.PGRES_TUPLES_OK) {
            std.log.err("query error: {s}", .{c.PQerrorMessage(self.conn)});
            c.PQclear(result);
            return error.QueryFailed;
        }
        return result;
    }

    /// Execute a query that modifies data (INSERT/UPDATE/DELETE).
    pub fn exec(self: *Db, sql: [*:0]const u8) !void {
        const result = c.PQexec(self.conn, sql) orelse return error.OutOfMemory;
        defer c.PQclear(result);
        if (c.PQresultStatus(result) != c.PGRES_COMMAND_OK) {
            std.log.err("exec error: {s}", .{c.PQerrorMessage(self.conn)});
            return error.ExecFailed;
        }
    }

    /// Execute a parameterised query (safe against SQL injection).
    pub fn queryParams(
        self: *Db,
        sql: [*:0]const u8,
        params: []const [*:0]const u8,
    ) !*c.PGresult {
        const result = c.PQexecParams(
            self.conn,
            sql,
            @intCast(params.len),
            null,
            @ptrCast(params.ptr),
            null,
            null,
            0,
        ) orelse return error.OutOfMemory;
        if (c.PQresultStatus(result) != c.PGRES_TUPLES_OK) {
            std.log.err("query error: {s}", .{c.PQerrorMessage(self.conn)});
            c.PQclear(result);
            return error.QueryFailed;
        }
        return result;
    }
};
