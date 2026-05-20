# RF Explorer Linux Viewer

Visualizador Linux nativo para RF Explorer, com grafico em tempo real, ajuste de faixa e marcacao automatica de picos.

## Recursos

- conexao serial com RF Explorer em `500000` baud
- grafico ao vivo de frequencia x amplitude
- ajuste de `start/stop` em MHz
- ajuste de topo e base em dBm
- deteccao dos picos mais fortes
- linha de estimativa de piso de ruido
- leitura do pico principal, horario da captura e contagem de sweeps
- modo `Max Hold`
- modo `--headless-smoke` para validar o hardware sem abrir GUI

## Dependencias

- Python 3.10+
- `pyserial`
- `pyqtgraph`
- `PyQt5` ou `PyQt6`
- biblioteca oficial [`RFExplorer-for-Python`](https://github.com/RFExplorer/RFExplorer-for-Python)

O app procura a biblioteca nesta ordem:

1. caminho definido em `RFEXPLORER_LIB`
2. `./vendor/RFExplorer-for-Python`
3. `vendor/RFExplorer-for-Python` relativo ao script

Exemplo:

```bash
git clone https://github.com/RFExplorer/RFExplorer-for-Python.git vendor/RFExplorer-for-Python
```

## Como executar

```bash
./run_rfexplorer_linux_viewer.sh
```

Para escolher a porta:

```bash
./run_rfexplorer_linux_viewer.sh --port /dev/ttyUSB0
```

Teste rapido sem GUI:

```bash
./run_rfexplorer_linux_viewer.sh --headless-smoke 5 --port /dev/ttyUSB0
```

## Permissao de acesso serial

Se o usuario atual ainda nao tiver acesso a `/dev/ttyUSB0`, o launcher tenta pedir autenticacao administrativa via `pkexec` para aplicar uma ACL local no device e continuar.

A correcao permanente no Linux costuma ser adicionar o usuario ao grupo `dialout` e fazer novo login.
