// SPDX-License-Identifier: MIT
// Decompile only the camera glue and sensor routines from the original Image.
// @category Leopard
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.nio.file.*;
import java.io.*;
import java.util.*;

public class LeopardFunctions extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        List<String[]> targets = new ArrayList<>();
        Set<String> helpers = new HashSet<>(Arrays.asList("regmap_write", "regmap_read",
            "i2c_transfer", "msleep", "usleep_range", "gpio_request", "gpio_free",
            "gpio_direction_output", "__gpio_set_value", "of_get_named_gpio_flags",
            "of_property_read_variable_u32_array", "devm_kmalloc", "__devm_regmap_init_i2c",
            "_mcount", "printk", "dev_err", "regmap_util_write_table_8"));
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] p = line.trim().split("\\s+");
            if (p.length != 3) continue;
            boolean target = args.length > 2 ? p[2].startsWith(args[2]) :
                p[2].startsWith("max929x_") || p[2].startsWith("imx185_");
            Address a = toAddr(p[0]);
            if (!currentProgram.getMemory().contains(a)) continue;
            if (target || helpers.contains(p[2])) {
                createLabel(a, p[2], true);
                if (p[1].equalsIgnoreCase("t")) {
                    disassemble(a);
                    Function f = getFunctionAt(a);
                    if (f == null) f = createFunction(a, p[2]);
                    if (target && f != null) targets.add(p);
                }
            }
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(args[1])) {
            for (String[] p : targets) {
                Function f = getFunctionAt(toAddr(p[0]));
                out.println("/* " + p[0] + " " + p[2] + " */");
                DecompileResults r = decompiler.decompileFunction(f, 60, monitor);
                if (r.decompileCompleted()) out.println(r.getDecompiledFunction().getC());
                else out.println("/* FAILED: " + r.getErrorMessage() + " */");
            }
        } finally { decompiler.dispose(); }
    }
}
