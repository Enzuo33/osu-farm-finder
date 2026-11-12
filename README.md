# osu! Farm Finder

acha mapas de farm (osu! standard) calibrados pro seu nível.

1. lê seu perfil e seu top 100 pela API v2 do osu!
2. pega jogadores um pouco acima do seu rank e lê o top 100 deles
3. conta quais mapas+mods mais aparecem (= farm do seu nível)
4. calcula com rosu-pp quanto cada um daria pra você na sua acc
5. tira o que você já tem, o que é difícil demais e o que não entra no seu top
6. mostra tudo numa tabela

## rodar

```
pip install -r requirements.txt
copy config.example.json config.json   # client_id/secret em osu.ppy.sh/home/account/edit (OAuth)
python gui.py
```

## gerar .exe

```
pyinstaller "osu Farm Finder.spec"
```
