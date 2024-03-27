def compile_cpp(config, source, build_dir):
  obj = source.replace('.cpp', '.o')
  dep = source.replace('.cpp', '.d')
  return config.task(
    desc          = f"Compile {source}",
    command       = f"g++ -MMD -c {source} -o {build_dir}/{obj}",
    source_files  = [source],
    build_files   = [f"{build_dir}/{obj}"],
    build_deps    = [f"{build_dir}/{dep}"],
  )

def link_cpp(config, tasks, build_dir, binary):
  objs = [task.config.build_files[0] for task in tasks]
  return config.task(
    desc          = f"Link {' and '.join(objs)} into {build_dir}/{binary}",
    command       = f"g++ {' '.join(objs)} -o {build_dir}/{binary}",
    source_files  = tasks,
    build_files   = [f"{build_dir}/{binary}"],
  )
