def compile_cpp(source, config):
  obj = config.build_path + source.replace('.cpp', '.o')
  dep = config.build_path + source.replace('.cpp', '.d')
  result = config.task(
    desc          = f"Compile {source}",
    command       = f"g++ -MMD -c {source} -o {obj}",
    source_files  = source,
    build_files   = obj,
    build_deps    = dep,
  )
  return result

def link_cpp(tasks, binary, config):
  objs = [task.task_config.build_files for task in tasks]
  return config.task(
    desc          = f"Link {' and '.join(objs)} into {config.build_path + binary}",
    command       = f"g++ {' '.join(objs)} -o {config.build_path + binary}",
    source_files  = tasks,
    build_files   = binary,
  )
