CC = gcc
CFLAGS = -Wall -Wextra -O2 $(shell pkg-config --cflags sdl2 SDL2_ttf)
LDFLAGS = $(shell pkg-config --libs sdl2 SDL2_ttf) -lm

SRCS = firstboot.c pulse.c text.c
OBJS = $(SRCS:.c=.o)
BIN = intergen-firstboot

.PHONY: all clean run run-fullscreen run-loop

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) -o $@ $^ $(LDFLAGS)

%.o: %.c pulse.h text.h
	$(CC) $(CFLAGS) -c -o $@ $<

run: $(BIN)
	./$(BIN)

run-fullscreen: $(BIN)
	./$(BIN) --fullscreen

run-loop: $(BIN)
	./$(BIN) --loop

clean:
	rm -f $(OBJS) $(BIN)
