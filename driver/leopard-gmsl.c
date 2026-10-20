// SPDX-License-Identifier: GPL-2.0-only
// Standalone control for the LI-XNX-BOX-GMSL2's three MAX9296/MAX9295 pairs.
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include "leopard_tables.h"

static void delay_ms(unsigned ms)
{
    struct timespec t = {ms / 1000, (ms % 1000) * 1000000L};
    while (nanosleep(&t, &t) < 0 && errno == EINTR) {}
}

static int transfer(int fd, struct i2c_msg *messages, unsigned count)
{
    struct i2c_rdwr_ioctl_data data = {messages, count};
    for (unsigned attempt = 0; attempt < 5; attempt++) {
        int result = ioctl(fd, I2C_RDWR, &data);
        if (result == (int)count) return 0;
        if (result >= 0) { errno = EIO; return -1; }
        if (errno != EAGAIN && errno != EINTR) return -1;
        delay_ms(20);
    }
    return -1;
}

static int write_reg(int fd, unsigned addr, unsigned reg, unsigned val)
{
    uint8_t bytes[] = {reg >> 8, reg & 255, val};
    struct i2c_msg m = {.addr = addr, .len = sizeof(bytes), .buf = bytes};
    if (transfer(fd, &m, 1) == 0) return 0;
    fprintf(stderr, "write address=0x%02x register=0x%04x value=0x%02x: %s\n",
            addr, reg, val, strerror(errno));
    return -1;
}

static int read_reg(int fd, unsigned address, unsigned reg, uint8_t *value)
{
    uint8_t bytes[] = {reg >> 8, reg & 255};
    struct i2c_msg messages[] = {
        {.addr = address, .len = sizeof(bytes), .buf = bytes},
        {.addr = address, .flags = I2C_M_RD, .len = 1, .buf = value},
    };
    return transfer(fd, messages, 2);
}

/* The recovered setup leaves both links enabled, which makes the
 * deserializer's I2C unreliable once the cameras restart. Select link A only
 * (CTRL0 0x01, automatic link selection off), wait, and trust only the
 * readback. Unlike the kernel module, this cannot clear a stuck bus.
 * Returns the attempts used, or 0.
 */
static unsigned select_link_a(int fd, unsigned attempts)
{
    for (unsigned attempt = 0; attempt < attempts; attempt++) {
        uint8_t ctrl0[] = {0x00, 0x10, 0x01}, value;
        struct i2c_msg m = {.addr = 0x48, .len = sizeof(ctrl0), .buf = ctrl0};
        transfer(fd, &m, 1);
        delay_ms(20);
        if (read_reg(fd, 0x48, 0x0010, &value) || value != 0x01) continue;
        unsigned sample;
        for (sample = 0; sample < 3; sample++)
            if (read_reg(fd, 0x48, 0x000d, &value) || value != 0x94)
                break;
        if (sample == 3)
            return attempt + 1;
    }
    return 0;
}

/* Full reset for a deserializer that link A selection does not reach, as in
 * the kernel module. The reset turns automatic link selection back on, so
 * select link A after each one. Returns the resets used, or 0.
 */
static unsigned reset_deserializer(int fd)
{
    for (unsigned attempt = 0; attempt < 60; attempt++) {
        uint8_t reset[] = {0x00, 0x10, 0x80};
        struct i2c_msg m = {.addr = 0x48, .len = sizeof(reset), .buf = reset};
        transfer(fd, &m, 1);
        delay_ms(300);
        if (select_link_a(fd, 5))
            return attempt + 1;
    }
    return 0;
}

static int read_output(int fd, uint8_t *value)
{
    uint8_t bytes[] = {0x03, 0x32};
    struct i2c_msg m[] = {
        {.addr = 0x48, .len = 2, .buf = bytes},
        {.addr = 0x48, .flags = I2C_M_RD, .len = 1, .buf = value},
    };
    return transfer(fd, m, 2);
}

static int wait_serializer(int fd, unsigned address)
{
    uint8_t reg[] = {0, 0}, value;
    struct i2c_msg messages[] = {
        {.addr = address, .len = sizeof(reg), .buf = reg},
        {.addr = address, .flags = I2C_M_RD, .len = 1, .buf = &value},
    };
    for (unsigned attempt = 0; attempt < 60; attempt++) {
        if (transfer(fd, messages, 2) == 0) {
            if ((value & 0xfe) == address << 1) return 0;
            errno = ENODEV;
            return -1;
        }
        if (errno != EREMOTEIO && errno != EAGAIN) return -1;
        delay_ms(500);
    }
    return -1;
}

int main(int argc, char **argv)
{
    if (argc < 3 || argc > 4 ||
        (strcmp(argv[1], "init") && strcmp(argv[1], "status")) ||
        (argc == 4 && strcmp(argv[3], "--apply"))) {
        fprintf(stderr, "Usage: %s init BUS [--apply] | status BUS\n"
                "BUS is 30, 31 or 32. Stop all camera capture before init.\n"
                "init prints the sequence unless --apply is supplied.\n", argv[0]);
        return 2;
    }
    char *end;
    long bus = strtol(argv[2], &end, 10);
    if (!*argv[2] || *end || bus < 30 || bus > 32) {
        fprintf(stderr, "BUS must be 30, 31 or 32\n");
        return 2;
    }
    bool status = !strcmp(argv[1], "status"), apply = argc == 4;
    int fd = -1;
    if (status || apply) {
        char path[64];
        snprintf(path, sizeof(path), "/dev/i2c-%ld", bus);
        fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0) { perror(path); return 1; }
    }
    if (status) {
        uint8_t value;
        int rc = read_output(fd, &value);
        if (rc) perror("read deserializer output register");
        else printf("bus=%ld output=0x%02x camera_a=%s camera_b=%s\n", bus, value,
                    value & 0x20 ? "on" : "off", value & 0x40 ? "on" : "off");
        close(fd);
        return rc ? 1 : 0;
    }
    printf("bus=%ld address=0x48 reg=0x0010 value=0x01 delay_ms=20 verify=1\n", bus);
    if (apply) {
        delay_ms(500);
        unsigned used = select_link_a(fd, 100);
        if (used) {
            printf("link A selected after %u attempts\n", used);
        } else if ((used = reset_deserializer(fd))) {
            printf("link A selected after %u resets\n", used);
        } else {
            fprintf(stderr, "deserializer did not respond\n");
            close(fd);
            return 1;
        }
    }
    for (unsigned i = 0; i < sizeof(leopard_link_init)/sizeof(leopard_link_init[0]); i++) {
        const struct leopard_reg *r = &leopard_link_init[i];
        printf("bus=%ld address=0x%02x reg=0x%04x value=0x%02x delay_ms=%u\n",
               bus, r->address, r->reg, r->value, r->delay_ms);
        if (apply) {
            if (r->reg == 0 && wait_serializer(fd, r->address)) {
                perror("serializer address not ready");
                goto restore;
            }
            int rc = -1;
            for (unsigned retry = 0; retry < (i == 0 ? 60U : 1U); retry++) {
                rc = write_reg(fd, r->address, r->reg, r->value);
                if (!rc || (errno != EREMOTEIO && errno != EAGAIN)) break;
                if (i == 0) delay_ms(500);
            }
            if (rc) goto restore;
            delay_ms(r->delay_ms);
        }
    }
    if (fd >= 0) close(fd);
    return 0;
restore:
    // Like the kernel module, leave link A selected for the next start.
    if (!select_link_a(fd, 10))
        fprintf(stderr, "could not select link A for the next start\n");
    close(fd);
    return 1;
}
