import hancho

dummy_rule = hancho.Rule(
  command = "echo files_in[0] = {files_in[0]}"
)

dummy_rule(files_in = "build.ninja", files_out = "asdf")

hancho.build()
