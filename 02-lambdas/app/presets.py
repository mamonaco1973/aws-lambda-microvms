PRESETS = {
    "seed": """balance = 41
cursor = (n * n for n in range(1000))
print('First generator value:', next(cursor))
Path('note.txt').write_text('Alice was here', encoding='utf-8')
print('Memory-only balance:', balance)
print('Preloaded rows:', len(sales))
print('Monthly totals:', {m: round(v, 2) for m, v in totals.items()})""",
    "continue": """balance += 1
print('Memory-only balance:', balance)
print('Next generator value:', next(cursor))
print('Disk:', Path('note.txt').read_text(encoding='utf-8'))""",
    "inspect": """print('Has balance:', 'balance' in globals())
print('Has cursor:', 'cursor' in globals())
print('Has note:', Path('note.txt').exists())
print('Preloaded rows:', len(sales))""",
    "bob": """balance = 9000
Path('note.txt').write_text('Bob was here', encoding='utf-8')
print('Bob balance:', balance)
print('Bob file:', Path('note.txt').read_text(encoding='utf-8'))""",
    "failure": """raise RuntimeError('A tenant cell failed; the other VM should be unaffected')""",
    "kill": """import os
os._exit(17)""",
}
