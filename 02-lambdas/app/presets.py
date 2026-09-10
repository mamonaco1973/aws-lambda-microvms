"""Code snippets offered in each runtime's editor.

The two sets are deliberate translations of each other: same variable names,
same sequence of effects, same printed labels. Run them side by side and the
only difference on screen is the syntax -- which is the point, since the
platform underneath is identical.

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
}
