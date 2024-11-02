hello_greet = hancho(
    hancho.cc_library,
    name = "hello-greet",
    in_srcs = ["hello-greet.cc"],
    in_hdrs = ["hello-greet.h"],
)

hancho(
    hancho.cc_binary,
    name = "hello-world",
    in_srcs = ["hello-world.cc"],
    in_deps = [
        hello-greet,
        hancho.lib.hello-time
    ],
)
