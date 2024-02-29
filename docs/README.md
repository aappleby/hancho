# Basic syntax

```py
# examples/hello_world/build.hancho
import hancho

hancho.config.set(build_dir = "build")

compile = hancho.Rule(
  desc = "Compile {files_in} -> {files_out}",
  command = "g++ -c {files_in} -o {files_out}",
  files_out = "{swap_ext(files_in, '.o')}",
  depfile = "{swap_ext(files_in, '.d')}",
)

link = hancho.Rule(
  desc = "Link {files_in} -> {files_out}",
  command = "g++ {files_in} -o {files_out}",
)

main_o = compile("main.cpp")
main_app = link(main_o, "app")
```

# Special fields in hancho.Rule()

- ```base``` (Default: ```hancho.config```)
    - The rule this rule inherits from. Reading missing fields from a ```rule``` will check ```rule.base``` for the field if there is one, otherwise the missing field will read as ```None```
- ```build_dir``` (Default: ```None```)
    - The directory to put output files in.
- ```command``` (Default: ```None```)
    - The console command this rule should run, or
    - An asynchronous function that returns a list of absolute-path filenames.
- ```debug``` (Default: ```False```)
    - True if this rule should print debug information when run
- ```depfile``` (Default: ```None```)
    - A [GCC format](http://www.google.com/search?q=gcc+dependency+file+format) dependencies file
- ```deps``` (Default: ```None```)
    - A list of files this rule depends on to run that are not input files.
- ```desc``` (Default: ```"{files_in} -> {files_out}"```)
    - A description of what this rule does that will be printed when run.
- ```dryrun``` (Default: ```False```)
    - True if this rule should pretend to succeed, but actually do nothing.
- ```files_in``` (Default: ```[]```)
    - The list of files (or file promises) this rule accepts as input.
- ```files_out``` (Default: ```[]```)
    - The list of files this rule generates as output
- ```force``` (Default: ```False```)
    - True if this rule should _always_ run.
- ```meta_deps``` (Default: Autogenerated)
    - The list of .hancho files that were in scope when this rule was run.
    - Used to rebuilt targets when .hancho files change.
- ```quiet``` (Default: ```False```)
    - True if Hancho should print no output when run. Overrides ```verbose```
- ```verbose``` (Default: ```False```)
    - True if Hancho should print command lines and rebuild reasons when run.
- ```jobs``` (Default: ```os.cpu_count()```)
    - The number of console commands Hancho will run in parallel.

# Helper methods defined in hancho.config:

 - ```expand(rule, string)```
    - Converts a template string to a normal string by replacing {}s in the string with values taken from ```rule```.
 - ```join([strings])```
    - Joins a list of strings with spaces, used to pass lists of files to console commands
 - ```len([list])```
    - Same as Python's ```len()```
 - ```run_cmd(command)```
    - Runs a console command synchronously and returns its stdout with whitespace stripped. Used to inject text into other console commands.
 - ```swap_ext(filename, suffix)```
    - Replaces a file's suffix with the given one. Used for converting ```foo.c``` to ```foo.o```, etcetera.
 - ```color()```
    - Changes the color of text printed to stdout. Used for color-coding of build rules.