# Dev only: clear the kill flag and target locks, and remove any leftover chaos-* toxics.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Remove-Item -Force -ErrorAction SilentlyContinue "$root\runs\KILL"
Remove-Item -Force -ErrorAction SilentlyContinue "$root\runs\locks\*.lock"
$py = "$root\.venv\Scripts\python.exe"
& $py -c "from chaos.config import load_safety; from chaos.toxiproxy import Toxiproxy; s=load_safety(); tp=Toxiproxy(s['sandbox']['toxiproxy_url']); [tp.remove_toxic(t, n) for t in s['targets'] for n in tp.chaos_toxics(t)]; print('toxics clean')"
