# SPDX-License-Identifier: GPL-2.0-only
ifneq ($(KERNELRELEASE),)
obj-m += leopard_gmsl.o
obj-m += leopard_imx185.o
ccflags-y += -I$(srctree)/../nvidia/include
else
CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
all: leopard-gmsl
leopard-gmsl: leopard-gmsl.c leopard_tables.h
	$(CC) $(CFLAGS) -o $@ leopard-gmsl.c
modules:
	$(MAKE) -C $(KERNEL_SRC) O=$(KERNEL_OUT) M=$(CURDIR) ARCH=arm64 CROSS_COMPILE=$(CROSS_COMPILE) modules
.PHONY: all modules
endif
