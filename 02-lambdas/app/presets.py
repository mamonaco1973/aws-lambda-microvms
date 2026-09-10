"""Code snippets offered in each runtime's editor.

The sets are deliberate translations of each other: same variable names, same
sequence of effects, same printed labels. Run them side by side and the only
difference on screen is the syntax -- which is the point, since the platform
underneath is identical.

Bash has no generator, so a counter plus a function stands in; the function
definition surviving a resume is the same claim by other means. It also carries
one preset the others cannot: a background job that outlives the checkpoint.

These are conveniences, not a contract. The editor is free-form and the
controller executes whatever it is sent.
"""

PRESETS = {
    "python": {
        "seed": """balance = 41
cursor = (n * n for n in range(1000))
print('First generator value:', next(cursor))
Path('note.txt').write_text('Python was here', encoding='utf-8')
print('Memory-only balance:', balance)
print('Preloaded rows:', len(sales))""",

        "continue": """balance += 1
print('Memory-only balance:', balance)
print('Next generator value:', next(cursor))
print('Disk:', Path('note.txt').read_text(encoding='utf-8'))""",

        "inspect": """print('Has balance:', 'balance' in globals())
print('Has cursor:', 'cursor' in globals())
print('Has note:', Path('note.txt').exists())
print('Preloaded rows:', len(sales))""",

        "failure": """raise RuntimeError('A submitted cell failed; the other MicroVM is unaffected')""",

        "kill": """import os
os._exit(17)""",
    },

    "node": {
        "seed": """balance = 41;
cursor = (function* () { for (let n = 0; n < 1000; n++) yield n * n; })();
console.log('First generator value:', cursor.next().value);
fs.writeFileSync('note.txt', 'Node was here');
console.log('Memory-only balance:', balance);
console.log('Preloaded rows:', sales.length);""",

        "continue": """balance += 1;
console.log('Memory-only balance:', balance);
console.log('Next generator value:', cursor.next().value);
console.log('Disk:', fs.readFileSync('note.txt', 'utf8'));""",

        "inspect": """console.log('Has balance:', typeof balance !== 'undefined');
console.log('Has cursor:', typeof cursor !== 'undefined');
console.log('Has note:', fs.existsSync('note.txt'));
console.log('Preloaded rows:', sales.length);""",

        "failure": """throw new Error('A submitted cell failed; the other MicroVM is unaffected');""",

        "kill": """process.exit(17);""",
    },

    "bash": {
        "seed": """balance=41
cursor=0
next_square() { square=$((cursor * cursor)); cursor=$((cursor + 1)); }
next_square
echo "First counter value: ${square}"
echo 'Bash was here' > note.txt
echo "Memory-only balance: ${balance}"
echo "Preloaded rows: ${#sales[@]}"
""",

        "continue": """balance=$((balance + 1))
echo "Memory-only balance: ${balance}"
next_square
echo "Next counter value: ${square}"
echo "Disk: $(cat note.txt)"
""",

        "inspect": """[[ -v balance ]] && echo "Has balance: true" || echo "Has balance: false"
declare -F next_square >/dev/null && echo "Has next_square: true" || echo "Has next_square: false"
[[ -f note.txt ]] && echo "Has note: true" || echo "Has note: false"
echo "Preloaded rows: ${#sales[@]}"
""",

        # Bash-only. A live child process surviving a memory checkpoint is the
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
