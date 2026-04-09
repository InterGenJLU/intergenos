CC = gcc
CFLAGS = -Wall -Wextra -O2 $(shell pkg-config --cflags libdrm freetype2)
LDFLAGS = $(shell pkg-config --libs libdrm freetype2) -lm

SRCS = firstboot-drm.c drm_display.c ft_text.c pulse.c text.c
OBJS = $(SRCS:.c=.o)
BIN = intergen-firstboot

.PHONY: all clean install

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) -o $@ $^ $(LDFLAGS)

firstboot-drm.o: firstboot-drm.c drm_display.h ft_text.h pulse.h text.h
drm_display.o: drm_display.c drm_display.h
ft_text.o: ft_text.c ft_text.h
pulse.o: pulse.c pulse.h
text.o: text.c text.h pulse.h

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

install: $(BIN)
	install -Dm755 $(BIN) $(DESTDIR)/usr/bin/intergen-firstboot
	install -Dm644 intergen-firstboot.service $(DESTDIR)/etc/systemd/system/intergen-firstboot.service

clean:
	rm -f $(OBJS) $(BIN)
