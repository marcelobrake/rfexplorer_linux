# RF Explorer Linux Viewer

Visualizador Linux nativo para RF Explorer com foco em analise espectral ao vivo, historico 3D e identificacao transient de picos.

## O que mudou nesta versao

- grafico principal redesenhado com preenchimento em gradiente
- picos com timeout configuravel para desaparecerem automaticamente
- waterfall historico 3D inspirado em analisadores de espectro com visao temporal
- separacao em camadas seguindo clean architecture
- testes automatizados com cobertura medida

## Recursos

- conexao serial com RF Explorer em `500000` baud
- ajuste de `start/stop` em MHz
- ajuste de topo e base em dBm
- marcacao de picos ativos com expiracao configuravel
- linha de estimativa de piso de ruido
- historico 3D de sweeps recentes
- modo `Max Hold`
- modo `--headless-smoke` para validar o hardware sem abrir GUI

## Arquitetura

O codigo foi dividido em camadas:

- `rfexplorer_linux/domain`: entidades e regras puras de espectro, picos e historico
- `rfexplorer_linux/application`: orquestracao do estado do viewer
- `rfexplorer_linux/infrastructure`: integracao com serial e `RFExplorer-for-Python`
- `rfexplorer_linux/presentation`: UI Qt e renderizacao dos graficos
- `rfexplorer_linux/cli.py`: ponto de entrada da aplicacao

## Dependencias

- Python 3.10+
- `numpy`
- `pyserial`
- `matplotlib`
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

## Testes

Rodar os testes unitarios:

```bash
python3 -m unittest discover -s tests -v
```

Rodar cobertura na `.venv` local de ferramentas:

```bash
python3 -m venv .venv
./.venv/bin/pip install coverage numpy pyserial
./.venv/bin/coverage run -m unittest discover -s tests
./.venv/bin/coverage report
```

Cobertura atual da camada testavel: `92%`.

## Permissao de acesso serial

Se o usuario atual ainda nao tiver acesso a `/dev/ttyUSB0`, o launcher tenta pedir autenticacao administrativa via `pkexec` para aplicar uma ACL local no device e continuar.

A correcao permanente no Linux costuma ser adicionar o usuario ao grupo `dialout` e fazer novo login.

## Licenca

Este projeto esta sob a Apache License 2.0. Veja [LICENSE](LICENSE) e [NOTICE](NOTICE).
