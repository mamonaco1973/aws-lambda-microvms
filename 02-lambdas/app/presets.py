"""Code snippets offered in each runtime's editor.

The sets are deliberate translations of each other: same variable names, same
values, same printed labels, same output. Run them side by side and the only
difference on screen is the syntax -- which is the point, since the platform
underneath is identical.

The demonstration is deliberately dull. "Seed state" assigns a few ordinary
variables and writes a file; "Check state" prints them back after a suspend and
a resume. Nothing is saved and nothing is reloaded in between, so the only
explanation for the values still being there is that it is the same process.
`rows` comes from the dataset built during the image build, so the same cell
also shows that build-time memory survived into the session.

These are conveniences, not a contract. The editor is free-form and the
controller executes whatever it is sent.
"""

PRESETS = {
    "python": {
        "seed": """user = 'mike'
count = 7
items = ['alpha', 'beta', 'gamma']
Path('note.txt').write_text('written before the suspend', encoding='utf-8')
print('Variables set. Now suspend the MicroVM.')""",

        "check": """print('user  =', user)
print('count =', count)
print('items =', ' '.join(items))
print('file  =', Path('note.txt').read_text(encoding='utf-8'))
print('rows  =', len(sales))""",

        "failure": """raise RuntimeError('A submitted cell failed; the other MicroVMs are unaffected')""",

        "kill": """import os
os._exit(17)""",
    },

    "node": {
        "seed": """user = 'mike';
count = 7;
items = ['alpha', 'beta', 'gamma'];
fs.writeFileSync('note.txt', 'written before the suspend');
console.log('Variables set. Now suspend the MicroVM.');""",

        "check": """console.log('user  =', user);
console.log('count =', count);
console.log('items =', items.join(' '));
console.log('file  =', fs.readFileSync('note.txt', 'utf8'));
console.log('rows  =', sales.length);""",

        "failure": """throw new Error('A submitted cell failed; the other MicroVMs are unaffected');""",

        "kill": """process.exit(17);""",
    },

    "bash": {
        "seed": """user=mike
count=7
items=(alpha beta gamma)
echo 'written before the suspend' > note.txt
echo 'Variables set. Now suspend the MicroVM.'""",

        "check": """echo "user  = ${user}"
echo "count = ${count}"
echo "items = ${items[*]}"
echo "file  = $(cat note.txt)"
echo "rows  = ${#sales[@]}"
""",

        # Bash only. A live child process surviving a memory checkpoint is the
        # most direct evidence in the project that the VM really was frozen.
        "background": """if [[ -v job ]] && kill -0 "${job}" 2>/dev/null; then
  echo "Background job ${job} is still alive"
  echo "Age since it started: $(ps -o etime= -p "${job}" | tr -d ' ')"
else
  sleep 3000 &
  job=$!
  echo "Started background job ${job}; suspend the VM, wake it, and run this again"
fi""",

        "failure": """echo 'A submitted cell failed; the other MicroVMs are unaffected' >&2
false""",

        "kill": """exit 17""",
    },
}
