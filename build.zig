const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{});

    const mod = b.createModule(.{
        .root_source_file = b.path("src/main.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    const target_os = target.result.os.tag;
    if (target_os == .macos) {
        mod.addIncludePath(.{ .cwd_relative = "/opt/homebrew/include" });
        mod.addLibraryPath(.{ .cwd_relative = "/opt/homebrew/lib" });
    } else {
        mod.addIncludePath(.{ .cwd_relative = "/usr/include/postgresql" });
    }
    mod.linkSystemLibrary("pq", .{});

    const exe = b.addExecutable(.{
        .name = "backend",
        .root_module = mod,
    });

    b.installArtifact(exe);

    const run_cmd = b.addRunArtifact(exe);
    run_cmd.step.dependOn(b.getInstallStep());
    if (b.args) |args| run_cmd.addArgs(args);

    const run_step = b.step("run", "Run the backend server");
    run_step.dependOn(&run_cmd.step);

    const auth_mod = b.createModule(.{
        .root_source_file = b.path("src/auth.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    const auth_test_mod = b.createModule(.{
        .root_source_file = b.path("tests/auth_test.zig"),
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    auth_test_mod.addImport("auth", auth_mod);
    const auth_tests = b.addTest(.{
        .root_module = auth_test_mod,
    });
    const run_auth_tests = b.addRunArtifact(auth_tests);
    const test_step = b.step("test", "Run unit tests");
    test_step.dependOn(&run_auth_tests.step);
}
