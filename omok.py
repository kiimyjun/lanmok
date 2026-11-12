import argparse
import getpass
import json
import os
import socket
import threading
import time

SIZE = 15
GAME_PORT = 50007      # TCP: 실제 게임 통신
DISC_PORT = 50008      # UDP: 방 찾기
APP_ID = "lan-omok"
COLS = "ABCDEFGHIJKLMNO"
EMPTY, BLACK, WHITE = 0, 1, 2
SYM = {BLACK: "\033[1;36mX\033[0m", WHITE: "\033[1;33mO\033[0m"}
COLOR_NAME = {BLACK: "흑(X)", WHITE: "백(O)"}


# 네트워크
class Conn:

    def __init__(self, sock):
        self.sock = sock
        self.reader = sock.makefile("r", encoding="utf-8")

    def send(self, **msg):
        try:
            data = json.dumps(msg, ensure_ascii=False) + "\n"
            self.sock.sendall(data.encode("utf-8"))
        except OSError:
            pass

    def recv(self):
        try:
            line = self.reader.readline()
        except OSError:
            return None
        if not line:
            return None
        try:
            return json.loads(line)
        except ValueError:
            return None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # 실제로 패킷을 보내지 않고 경로만 확인
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def beacon(name, port, stop):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    msg = json.dumps({"app": APP_ID, "name": name, "port": port}).encode()
    while not stop.is_set():
        try:
            u.sendto(msg, ("<broadcast>", DISC_PORT))
        except OSError:
            pass
        stop.wait(1)
    u.close()


def discover(timeout=3.0):
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    u.bind(("", DISC_PORT))
    u.settimeout(0.5)
    rooms = {}
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, addr = u.recvfrom(1024)
        except socket.timeout:
            continue
        try:
            info = json.loads(data)
        except ValueError:
            continue
        if info.get("app") == APP_ID:
            rooms[(addr[0], int(info["port"]))] = info.get("name", "?")
    u.close()
    return rooms


def parse_move(s):
    s = s.strip().upper().replace(" ", "")
    if len(s) < 2 or s[0] not in COLS or not s[1:].isdigit():
        return None
    x, y = COLS.index(s[0]), int(s[1:]) - 1
    if not 0 <= y < SIZE:
        return None
    return x, y


def is_win(board, x, y, color):
    for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
        count = 1
        for sign in (1, -1):
            nx, ny = x + dx * sign, y + dy * sign
            while 0 <= nx < SIZE and 0 <= ny < SIZE and board[ny][nx] == color:
                count += 1
                nx += dx * sign
                ny += dy * sign
        if count >= 5:
            return True
    return False


def board_text(board, last, viewer, names):
    lines = [f"  {COLOR_NAME[BLACK]}: {names[BLACK]}    {COLOR_NAME[WHITE]}: {names[WHITE]}"
             f"    (나는 {COLOR_NAME[viewer]})", "", "    " + " ".join(COLS)]
    for y in range(SIZE):
        cells = []
        for x in range(SIZE):
            v = board[y][x]
            ch = "." if v == EMPTY else SYM[v]
            if last == (x, y):
                ch = "\033[7m" + ch + "\033[0m" 
            cells.append(ch)
        lines.append(f"{y + 1:>3} " + " ".join(cells))
    return "\n".join(lines) + "\n\n"


def render(board, last, me, names):
    os.system("cls" if os.name == "nt" else "clear")
    print(board_text(board, last, me, names), end="")


def local_move(board):
    while True:
        s = input("당신 차례입니다 (예: H8, q = 나가기) > ")
        if s.strip().lower() in ("q", "quit", "exit"):
            return None
        pos = parse_move(s)
        if pos is None:
            print("  형식이 올바르지 않아요. 열(A~O)+행(1~15), 예: H8")
            continue
        x, y = pos
        if board[y][x] != EMPTY:
            print("  이미 돌이 있는 자리예요.")
            continue
        return pos


def play(conn, me, names):
    board = [[EMPTY] * SIZE for _ in range(SIZE)]
    turn, last, moves = BLACK, None, 0
    try:
        while True:
            render(board, last, me, names)
            if turn == me:
                pos = local_move(board)
                if pos is None:
                    conn.send(type="quit")
                    print("게임을 나갔습니다.")
                    return
                x, y = pos
                conn.send(type="move", x=x, y=y)
            else:
                print(f"{names[turn]} 님이 생각하는 중...")
                msg = conn.recv()
                if msg is None:
                    print("상대와의 연결이 끊어졌습니다.")
                    return
                if msg.get("type") == "quit":
                    print("상대가 게임을 나갔습니다.")
                    return
                if msg.get("type") != "move":
                    continue
                x, y = int(msg["x"]), int(msg["y"])
                if not (0 <= x < SIZE and 0 <= y < SIZE) or board[y][x] != EMPTY:
                    print("잘못된 수를 받았습니다. 게임을 종료합니다.")
                    return

            board[y][x] = turn
            last = (x, y)
            moves += 1
            if is_win(board, x, y, turn):
                render(board, last, me, names)
                print("승리" if turn == me else "패배")
                return
            if moves == SIZE * SIZE:
                render(board, last, me, names)
                print("무승부")
                return
            turn = WHITE if turn == BLACK else BLACK
    except (KeyboardInterrupt, EOFError):
        conn.send(type="quit")
        print("\n게임을 나갔습니다.")
    finally:
        conn.close()


def is_omok_client(sock):
    sock.settimeout(1.5)
    try:
        first = sock.recv(1, socket.MSG_PEEK)
    except OSError:
        first = b""
    sock.settimeout(None)
    return first == b"{"


def play_vs_nc(sock, host_name):
    reader = sock.makefile("r", encoding="utf-8", errors="replace", newline=None)
    clear = "\033[2J\033[H"

    def out(text):
        try:
            sock.sendall(text.replace("\n", "\r\n").encode("utf-8"))
        except OSError:
            pass

    def ask(prompt):
        out(prompt)
        try:
            line = reader.readline()
        except OSError:
            return None
        return line.strip() if line else None

    out("CLI 오목에 오신 것을 환영합니다. (나가려면 q 또는 Ctrl+C)\n")
    guest = ask("이름을 입력하세요 > ")
    if guest is None:
        print("상대와의 연결이 끊어졌습니다.")
        return
    names = {BLACK: host_name, WHITE: guest or "손님"}
    board = [[EMPTY] * SIZE for _ in range(SIZE)]
    turn, last, moves = BLACK, None, 0

    try:
        while True:
            render(board, last, BLACK, names)
            if turn == BLACK:
                out(clear + board_text(board, last, WHITE, names)
                    + f"{host_name} 님이 생각하는 중...\n")
                pos = local_move(board)
                if pos is None:
                    out("\n상대가 게임을 나갔습니다.\n")
                    print("게임을 나갔습니다.")
                    return
            else:
                print(f"{names[WHITE]} 님이 생각하는 중...")
                out(clear + board_text(board, last, WHITE, names))
                while True:
                    s = ask("당신 차례입니다 (예: H8, q = 나가기) > ")
                    if s is None:
                        print("상대와의 연결이 끊어졌습니다.")
                        return
                    if s.lower() in ("q", "quit", "exit"):
                        out("게임을 나갔습니다.\n")
                        print("상대가 게임을 나갔습니다.")
                        return
                    pos = parse_move(s)
                    if pos is None:
                        out("  형식이 올바르지 않아요. 열(A~O)+행(1~15), 예: H8\n")
                    elif board[pos[1]][pos[0]] != EMPTY:
                        out("  이미 돌이 있는 자리예요.\n")
                    else:
                        break
            x, y = pos
            board[y][x] = turn
            last = (x, y)
            moves += 1
            won = is_win(board, x, y, turn)
            if won or moves == SIZE * SIZE:
                render(board, last, BLACK, names)
                final = clear + board_text(board, last, WHITE, names)
                if not won:
                    print("무승부")
                    out(final + "무승부\n")
                elif turn == BLACK:
                    print("승리")
                    out(final + "패배\n")
                else:
                    print("패배")
                    out(final + "승리\n")
                return
            turn = WHITE if turn == BLACK else BLACK
    except (KeyboardInterrupt, EOFError):
        out("\n상대가 게임을 나갔습니다.\n")
        print("\n게임을 나갔습니다.")
    finally:
        sock.close()


def host(name, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(1)

    stop = threading.Event()
    threading.Thread(target=beacon, args=(name, port, stop), daemon=True).start()
    ip = local_ip()
    print(f"방을 열었습니다 → {ip}:{port}  (방 이름: {name})")
    print("참가 방법을 상대에게 알려 주세요:")
    print(f"  해당 명령어를 입력     : nc {ip} {port}")
    print("상대가 참가하기를 기다리는 중... (Ctrl+C로 취소)")
    try:
        sock, addr = srv.accept()
    finally:
        stop.set()
        srv.close()

    if not is_omok_client(sock):
        play_vs_nc(sock, name)
        return
    conn = Conn(sock)
    conn.send(type="hello", name=name)
    hello = conn.recv()
    if not hello or hello.get("type") != "hello":
        print("상대와 연결하지 못했습니다.")
        conn.close()
        return
    play(conn, BLACK, {BLACK: name, WHITE: hello.get("name", "상대")})


def join(target, name):
    if target:
        ip, _, p = target.partition(":")
        port = int(p) if p else GAME_PORT
    else:
        print("같은 네트워크에서 열린 방을 찾는 중...")
        rooms = list(discover().items())
        if not rooms:
            print("방을 찾지 못했어요. IP를 직접 입력해 보세요: python omok.py join 192.168.0.10")
            return
        for i, ((ip, port), rname) in enumerate(rooms, 1):
            print(f"  {i}. {rname}  ({ip}:{port})")
        choice = input("참가할 방 번호 > ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(rooms):
            print("취소했습니다.")
            return
        (ip, port), _ = rooms[int(choice) - 1]

    try:
        sock = socket.create_connection((ip, port), timeout=5)
    except OSError as e:
        print(f"{ip}:{port} 에 연결하지 못했습니다: {e}")
        return
    sock.settimeout(None)

    conn = Conn(sock)
    conn.send(type="hello", name=name)
    hello = conn.recv()
    if not hello or hello.get("type") != "hello":
        print("상대와 연결하지 못했습니다.")
        conn.close()
        return
    play(conn, WHITE, {BLACK: hello.get("name", "상대"), WHITE: name})


def main():
    if os.name == "nt":
        os.system("")  # Windows 터미널에서 ANSI 색상 활성화

    try:
        default_name = getpass.getuser()
    except Exception:
        default_name = "플레이어"

    ap = argparse.ArgumentParser(description="LAN 오목")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("host", help="방 만들기 (흑, 선공)")
    h.add_argument("--name", default=default_name)
    h.add_argument("--port", type=int, default=GAME_PORT)
    j = sub.add_parser("join", help="방 참가하기 (백)")
    j.add_argument("target", nargs="?", help="호스트 IP[:포트] (생략 시 자동 검색)")
    j.add_argument("--name", default=default_name)
    args = ap.parse_args()

    try:
        if args.cmd == "host":
            host(args.name, args.port)
        else:
            join(args.target, args.name)
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()