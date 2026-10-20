/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef LEOPARD_GMSL_H
#define LEOPARD_GMSL_H
#include <linux/types.h>
int leopard_gmsl_ready(unsigned int channel);
int leopard_gmsl_stream(unsigned int camera, bool enable);
#endif
