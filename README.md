# Hancho

"班長, hanchō - "Squad leader”, from 19th c. Mandarin 班長 (bānzhǎng, “team leader”)
Probably entered English during World War II: many apocryphal stories describe American soldiers hearing Japanese prisoners-of-war refer to their lieutenants as hanchō."

Hancho is the smallest build system I can make that fits my needs.
It focuses on a small set of features:

1. Easy construction of commands via Python f-strings
2. Minimal, parallel, fast rebuilds.
3. Zero "magic spells" - everything is completely explicit.

Hancho should suffice for small to medium sized projects.



Hancho Rules

Hancho Files

Hancho modules are just Python modules with a .hancho suffix. There are a few minor differences to be aware of:

1. Hancho modules are loaded via "module = hancho.load('{filename}')", where {filename} can be
a. A filename, relative to the Hancho root or the current directory
b. A directory name
Given a filename string, Hancho will try and load
a. The file with that name, if it exists.
b. If 'path' is a directory like 'src/stuff', it will look for 'src/stuff/stuff.hancho'
b. If 'path' is a directory like 'src/stuff', it will look for 'src/stuff/build.hancho'

1. Hancho changes the working directory to the directory holding the .hancho file before loading it. This means that calling things like 'glob.glob("*.cpp")' in the .hancho file will search for .cpp files in the same directory as the .hancho file.

2. Hancho modules aren't added to sys.modules.

Hancho Templates
