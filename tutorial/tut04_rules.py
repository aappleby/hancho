import hancho

def compile_cpp(source, config):
  build_path = config.build_path
  obj = source.replace('.cpp', '.o')
  dep = source.replace('.cpp', '.d')
  return hancho.Task(
    config        = config,
    desc          = f"Compile {source}",
    command       = f"g++ -MMD -c {source} -o {build_path}/{obj}",
    source_files  = source,
    build_files   = obj,
    build_deps    = dep,
  )

def link_cpp(tasks, binary, config):
  build_path = config.build_path
  objs = [task.config.build_files for task in tasks]
  obj_paths = [f"{build_path}/{obj}" for obj in objs]
  result = hancho.Task(
    config        = config,
    desc          = f"Link {objs} into {binary}",
    command       = f"g++ {' '.join(obj_paths)} -o {build_path}/{binary}",
    source_files  = tasks,
    build_files   = binary,
  )
