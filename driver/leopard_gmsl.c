// SPDX-License-Identifier: GPL-2.0-only
// LI-XNX-BOX-GMSL2 link configuration, reconstructed from observed I2C traffic.
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/i2c-mux.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pm_runtime.h>
#include "leopard_gmsl.h"
#include "leopard_tables.h"

struct leopard_bridge {
    struct i2c_client *client;
    unsigned int channel;
};

static DEFINE_MUTEX(bridge_lock);
static struct leopard_bridge *bridges[3];
// Set while channel zero holds the shared camera power.
static bool camera_power;

/* The Tegra controller implements the standard nine-clock bus clear, but R35
 * invokes it only after arbitration loss, not after a transfer timeout. Clear
 * the bus before each full reset, and after a timeout while selecting link A,
 * on the first link command or on a read-only readiness check; never replay a
 * failed address change.
 */
static int leopard_recover_bus(struct i2c_client *client)
{
    struct i2c_adapter *root = i2c_root_adapter(&client->dev);
    int ret;
    if (!root)
        return -ENODEV;
    i2c_lock_bus(root, I2C_LOCK_ROOT_ADAPTER);
    ret = pm_runtime_resume_and_get(root->dev.parent);
    if (ret >= 0) {
        ret = i2c_recover_bus(root);
        pm_runtime_put(root->dev.parent);
    }
    i2c_unlock_bus(root, I2C_LOCK_ROOT_ADAPTER);
    /* Tegra returns EAGAIN after successful bus clear to request a retry. */
    if (ret == -EAGAIN)
        ret = 0;
    if (!ret)
        dev_info(&client->dev, "recovered camera control bus\n");
    return ret;
}

static int leopard_transfer(struct i2c_adapter *adapter, struct i2c_msg *msgs, int n)
{
    int ret, attempt;
    for (attempt = 0; attempt < 5; attempt++) {
        ret = i2c_transfer(adapter, msgs, n);
        if (ret == n)
            return 0;
        if (ret != -EAGAIN)
            return ret < 0 ? ret : -EIO;
        msleep(20);
    }
    return ret;
}

static int leopard_write(struct i2c_client *client, u8 addr, u16 reg, u8 value)
{
    u8 buf[] = {reg >> 8, reg & 0xff, value};
    struct i2c_msg msg = {.addr = addr, .len = sizeof(buf), .buf = buf};
    int ret = leopard_transfer(client->adapter, &msg, 1);
    if (ret)
        dev_err(&client->dev, "address 0x%02x register 0x%04x write failed: %d\n", addr, reg, ret);
    return ret;
}

static int leopard_read(struct i2c_client *client, u16 reg, u8 *value)
{
    u8 buf[] = {reg >> 8, reg & 0xff};
    struct i2c_msg msgs[] = {
        {.addr = 0x48, .len = sizeof(buf), .buf = buf},
        {.addr = 0x48, .flags = I2C_M_RD, .len = 1, .buf = value},
    };
    return leopard_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
}

// Deserializer CTRL0 (0x0010): link A only, automatic link selection off.
static int leopard_force_link_a(struct i2c_client *client)
{
    u8 buf[] = {0x00, 0x10, 0x01};
    struct i2c_msg msg = {.addr = 0x48, .len = sizeof(buf), .buf = buf};
    return leopard_transfer(client->adapter, &msg, 1);
}

/* The recovered setup leaves CTRL0 = 0x23: both links enabled. The
 * deserializer keeps its registers through a reboot while the cameras
 * restart, and with both links enabled and the serializers unconfigured its
 * I2C answers unreliably: NAKs, corrupted data and a held clock. Automatic
 * link selection, the reset default, may choose link B, so turn it off and
 * select link A. A write can land without an acknowledgement and vice
 * versa, and reading back at once needed many more attempts in testing:
 * wait, then trust only the readback. Returns the attempts used or an error.
 */
static int leopard_select_link_a(struct i2c_client *client, unsigned int attempts)
{
    unsigned int attempt, sample;
    u8 value;
    int ret = 0;

    for (attempt = 0; attempt < attempts; attempt++) {
        if (ret == -ETIMEDOUT)
            leopard_recover_bus(client);
        ret = leopard_force_link_a(client);
        msleep(20);
        if (ret == -ETIMEDOUT)
            continue;
        ret = leopard_read(client, 0x0010, &value);
        if (ret || value != 0x01)
            continue;
        for (sample = 0; sample < 3; sample++) {
            ret = leopard_read(client, 0x000d, &value);
            if (ret || value != 0x94)
                break;
        }
        if (sample == 3)
            return attempt + 1;
    }
    return ret ? ret : -EIO;
}

/* Full reset for a deserializer that link A selection does not reach. The
 * reset turns automatic link selection back on, and checking the reset value
 * 0x11 failed 60 times in a row on a pair that link A selection then reached
 * at once, so select link A after each reset and trust only that readback.
 * The reset write's acknowledgement is unreliable in the bad state. Pairs
 * needed 1 to 24 resets after hard reboots.
 * Returns the resets used or an error.
 */
static int leopard_reset_deserializer(struct i2c_client *client)
{
    u8 buf[] = {0x00, 0x10, 0x80};
    struct i2c_msg msg = {.addr = 0x48, .len = sizeof(buf), .buf = buf};
    unsigned int attempt;
    int ret = 0;

    for (attempt = 0; attempt < 60; attempt++) {
        // A bus clear can fail while the clock is held; keep trying.
        leopard_recover_bus(client);
        leopard_transfer(client->adapter, &msg, 1);
        msleep(300);
        ret = leopard_select_link_a(client, 5);
        if (ret > 0)
            return attempt + 1;
    }
    return ret;
}

/* Leave the deserializer on link A only, so that its I2C keeps working while
 * the cameras restart before the next start. Call with bridge_lock held.
 */
static void leopard_leave_link_a(struct i2c_client *client)
{
    if (leopard_select_link_a(client, 10) < 0)
        dev_warn(&client->dev, "could not select link A for the next start\n");
}

static int leopard_wait_serializer(struct i2c_client *client, u8 address)
{
    u8 reg[] = {0, 0}, value;
    struct i2c_msg msgs[] = {
        {.addr = address, .len = sizeof(reg), .buf = reg},
        {.addr = address, .flags = I2C_M_RD, .len = 1, .buf = &value},
    };
    unsigned int attempt;
    int ret = -ETIMEDOUT;
    /* Link selection can acknowledge before the remote control channel is
     * usable. Poll the serializer's own address register before changing it;
     * repeating an address-changing write would target a different device.
     */
    for (attempt = 0; attempt < 60; attempt++) {
        ret = leopard_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
        if (!ret)
            return (value & 0xfe) == address << 1 ? 0 : -ENODEV;
        if (ret == -ETIMEDOUT) {
            leopard_recover_bus(client);
        } else if (ret != -EREMOTEIO && ret != -EAGAIN) {
            return ret;
        }
        msleep(500);
    }
    dev_err(&client->dev, "serializer 0x%02x did not become ready\n", address);
    return ret;
}

int leopard_gmsl_ready(unsigned int channel)
{
    int ret;
    if (channel >= ARRAY_SIZE(bridges))
        return -EINVAL;
    mutex_lock(&bridge_lock);
    ret = bridges[channel] ? 0 : -EPROBE_DEFER;
    mutex_unlock(&bridge_lock);
    return ret;
}
EXPORT_SYMBOL_GPL(leopard_gmsl_ready);

int leopard_gmsl_stream(unsigned int camera, bool enable)
{
    struct leopard_bridge *bridge;
    u8 value, mask;
    int ret;
    if (camera >= 6)
        return -EINVAL;
    mutex_lock(&bridge_lock);
    bridge = bridges[camera / 2];
    if (!bridge) { ret = -ENODEV; goto done; }
    ret = leopard_read(bridge->client, 0x0332, &value);
    if (ret)
        goto done;
    mask = camera % 2 ? 0x40 : 0x20;
    value = enable ? value | mask : value & ~mask;
    ret = leopard_write(bridge->client, 0x48, 0x0332, value);
done:
    mutex_unlock(&bridge_lock);
    return ret;
}
EXPORT_SYMBOL_GPL(leopard_gmsl_stream);

/* Select link A, fully resetting the deserializer if needed, and run the
 * recovered link setup. Call with bridge_lock held.
 */
static int leopard_start_pair(struct i2c_client *client)
{
    unsigned int i, retry;
    int ret;
    ret = leopard_select_link_a(client, 100);
    if (ret > 0) {
        dev_info(&client->dev, "link A selected after %d attempts\n", ret);
    } else {
        dev_warn(&client->dev, "link A not selected, resetting the deserializer\n");
        ret = leopard_reset_deserializer(client);
        if (ret < 0) {
            dev_err(&client->dev, "deserializer did not respond: %d\n", ret);
            return ret;
        }
        dev_info(&client->dev, "link A selected after %d resets\n", ret);
    }
    for (i = 0; i < ARRAY_SIZE(leopard_link_init); i++) {
        const struct leopard_reg *r = &leopard_link_init[i];
        if (r->reg == 0) {
            ret = leopard_wait_serializer(client, r->address);
            if (ret)
                goto restore;
        }
        // The original kernel can reach this before the shared bridge power is
        // settled. Only retry the first link-select command; address-changing
        // writes later in the sequence must never be blindly repeated.
        for (retry = 0; retry < (i == 0 ? 60 : 1); retry++) {
            ret = leopard_write(client, r->address, r->reg, r->value);
            if (i == 0 && ret == -ETIMEDOUT) {
                int recovered = leopard_recover_bus(client);
                if (recovered) {
                    ret = recovered;
                    break;
                }
                ret = -EAGAIN;
            }
            if (!ret || (ret != -EREMOTEIO && ret != -EAGAIN))
                break;
            if (i == 0)
                msleep(500);
        }
        if (ret)
            goto restore;
        if (r->delay_ms)
            msleep(r->delay_ms);
    }
    return 0;
restore:
    leopard_leave_link_a(client);
    return ret;
}

static int leopard_probe(struct i2c_client *client, const struct i2c_device_id *id)
{
    struct leopard_bridge *bridge;
    struct gpio_desc *power;
    unsigned int channel;
    int ret;
    if (client->addr != 0x48 || !i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
        return -ENODEV;
    ret = of_property_read_u32(client->dev.of_node, "channel", &channel);
    if (ret || channel >= ARRAY_SIZE(bridges))
        return -EINVAL;
    bridge = devm_kzalloc(&client->dev, sizeof(*bridge), GFP_KERNEL);
    if (!bridge)
        return -ENOMEM;
    bridge->client = client;
    bridge->channel = channel;
    // The board has one shared power-enable GPIO, owned only by channel zero.
    if (channel == 0) {
        power = devm_gpiod_get_optional(&client->dev, "pwdn", GPIOD_OUT_HIGH);
        if (IS_ERR(power))
            return PTR_ERR(power);
    }
    mutex_lock(&bridge_lock);
    if (channel != 0 && !camera_power) { ret = -EPROBE_DEFER; goto done; }
    if (bridges[channel]) { ret = -EBUSY; goto done; }
    if (channel == 0)
        camera_power = true;
    // Like the original max929x_probe, wait 500 ms after enabling power.
    msleep(500);
    ret = leopard_start_pair(client);
    if (!ret) {
        bridges[channel] = bridge;
        dev_info(&client->dev, "IMX185 GMSL2 pair %u ready\n", channel);
    } else if (channel == 0) {
        // Stay bound and keep the camera power on for the other pairs.
        dev_err(&client->dev, "IMX185 GMSL2 pair 0 failed: %d\n", ret);
        ret = 0;
    }
    if (!ret)
        i2c_set_clientdata(client, bridge);
done:
    mutex_unlock(&bridge_lock);
    return ret;
}

static void leopard_shutdown(struct i2c_client *client)
{
    mutex_lock(&bridge_lock);
    leopard_leave_link_a(client);
    mutex_unlock(&bridge_lock);
}

static int leopard_remove(struct i2c_client *client)
{
    struct leopard_bridge *bridge = i2c_get_clientdata(client);
    if (!bridge)
        return 0;
    mutex_lock(&bridge_lock);
    if (bridges[bridge->channel] == bridge) {
        leopard_write(client, 0x48, 0x0332, 0);
        bridges[bridge->channel] = NULL;
    }
    leopard_leave_link_a(client);
    if (bridge->channel == 0)
        camera_power = false;
    mutex_unlock(&bridge_lock);
    return 0;
}

static const struct of_device_id leopard_of_match[] = {
    {.compatible = "leopard,li-max929x"}, {}
};
MODULE_DEVICE_TABLE(of, leopard_of_match);
static const struct i2c_device_id leopard_ids[] = {{"leopard-max929x", 0}, {}};
MODULE_DEVICE_TABLE(i2c, leopard_ids);
static struct i2c_driver leopard_driver = {
    .driver = {.name = "leopard-max929x", .of_match_table = leopard_of_match},
    .probe = leopard_probe,
    .remove = leopard_remove,
    .shutdown = leopard_shutdown,
    .id_table = leopard_ids,
};
module_i2c_driver(leopard_driver);
MODULE_DESCRIPTION("Leopard LI-XNX-BOX-GMSL2 bridge initialization and CSI gating");
MODULE_LICENSE("GPL v2");
