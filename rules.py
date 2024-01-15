import tinybuild

tinybuild.global_args.toolchain  = "x86_64-linux-gnu"
tinybuild.global_args.build_type = "-g -O0"
tinybuild.global_args.warnings   = "-Wunused-variable -Werror"
tinybuild.global_args.depfile    = "-MMD -MF {file_out}.d"
tinybuild.global_args.defines    = "-DCONFIG_DEBUG"
tinybuild.global_args.cpp_std    = "-std=gnu++2a"
tinybuild.global_args.includes   = "-I. -Isymlinks"
tinybuild.global_args.c_opts     = "{warnings} {depfile} {build_type}"
tinybuild.global_args.cpp_opts   = "{cpp_std} {c_opts}"
tinybuild.global_args.ld_opts    = "{build_type}"

compile_cpp = tinybuild.map(
  #desc      = "Compiling C++ {file_in} => {file_out}",
  command   = "{toolchain}-g++ {cpp_opts} {includes} {defines} -c {file_in} -o {file_out}",
)

compile_c   = tinybuild.map(
  #desc      = "Compiling C {file_in} => {file_out}",
  command   = "{toolchain}-gcc {c_opts} {includes} {defines} -c {file_in} -o {file_out}",
)

link_c_lib  = tinybuild.reduce(
  #desc      = "Bundling {file_out}",
  command   = "ar rcs {file_out} {join(files_in)}",
)

link_c_bin  = tinybuild.reduce(
  #desc      = "Linking {file_out}",
  command   = "{toolchain}-g++ {ld_opts} {join(files_in)} {libraries} -o {file_out}",
)
