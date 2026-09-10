"""Code snippets offered in each runtime's editor.

The sets are deliberate translations of each other: same variable names, same
values, same printed labels, same output. Run them side by side and the only
difference on screen is the syntax -- which is the point, since the platform
underneath is identical.

The demonstration is deliberately dull, and the order matters. "Check state"
runs first on a freshly launched MicroVM and finds `user`, `visits` and `items`
already set, because they were assigned during the image build and live in the
snapshot. "Update state" then mutates them. Suspend, resume, check again and
the mutations are still there; terminate, launch and check again and they are
gone while the build-time values are back. Same cell, three different answers,
which is the whole distinction between image memory and session memory.

These are conveniences, not a contract. The editor is free-form and the
controller executes whatever it is sent.
"""

PRESETS = {
    "python": {
        "check": """print('user   =', user)
print('visits =', visits)
print('items  =', ' '.join(items))
print('file   =', Path('note.txt').read_text(encoding='utf-8')
      if Path('note.txt').is_file() else '(none)')""",

        "update": """user = 'mike'
visits += 1
items.append('item' + str(visits))
Path('note.txt').write_text('updated ' + str(visits) + ' time(s)', encoding='utf-8')
print('State updated. Check it, or suspend and come back to it.')""",

        "failure": """raise RuntimeError('A submitted cell failed; the other MicroVMs are unaffected')""",

        "kill": """import os
os._exit(17)""",
    },

    "node": {
        "check": """console.log('user   =', user);
console.log('visits =', visits);
console.log('items  =', items.join(' '));
console.log('file   =', fs.existsSync('note.txt')
  ? fs.readFileSync('note.txt', 'utf8') : '(none)');""",

        "update": """user = 'mike';
visits += 1;
items.push('item' + visits);
fs.writeFileSync('note.txt', 'updated ' + visits + ' time(s)');
console.log('State updated. Check it, or suspend and come back to it.');""",

        "failure": """throw new Error('A submitted cell failed; the other MicroVMs are unaffected');""",

        "kill": """process.exit(17);""",
    },

    "bash": {
        "check": """echo "user   = ${user}"
echo "visits = ${visits}"
echo "items  = ${items[*]}"
echo "file   = $(cat note.txt 2>/dev/null || echo '(none)')"
""",

        "update": """user=mike
visits=$((visits + 1))
items+=("item${visits}")
echo "updated ${visits} time(s)" > note.txt
echo 'State updated. Check it, or suspend and come back to it.'""",

        "failure": """echo 'A submitted cell failed; the other MicroVMs are unaffected' >&2
false""",

        "kill": """exit 17""",
    },
}
