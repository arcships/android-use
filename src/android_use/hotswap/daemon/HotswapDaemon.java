import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.PrintStream;
import java.lang.reflect.Method;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;

/**
 * Keeps javac, the Kotlin compiler and d8 loaded in one JVM so a hot swap does not pay a
 * JVM start per tool. Loopback only; every request must carry the token from the port
 * file, which only this user can read. Exits after 20 idle minutes.
 *
 * Request:  token, tool, argument count, arguments   (strings: int length + UTF-8)
 * Response: int exit code, int length, output bytes
 */
public class HotswapDaemon {
    private static final Map<String, ClassLoader> kotlinLoaders = new HashMap<>();

    public static void main(String[] args) throws Exception {
        Path portFile = Paths.get(args[0]);
        byte[] random = new byte[24];
        new SecureRandom().nextBytes(random);
        StringBuilder token = new StringBuilder();
        for (byte b : random) token.append(String.format("%02x", b));
        try (ServerSocket server = new ServerSocket(0, 8, InetAddress.getLoopbackAddress())) {
            server.setSoTimeout(20 * 60 * 1000);
            Path temporary = Paths.get(args[0] + ".tmp");
            Files.deleteIfExists(temporary);
            Files.createFile(temporary, PosixFilePermissions.asFileAttribute(
                    PosixFilePermissions.fromString("rw-------")));
            Files.write(temporary, (server.getLocalPort() + " " + token + "\n").getBytes());
            Files.move(temporary, portFile, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            while (true) {
                try (Socket socket = server.accept()) {
                    serve(socket, token.toString());
                } catch (SocketTimeoutException idle) {
                    break;
                } catch (Exception broken) {
                    // A bad client must not take the daemon down.
                }
            }
        } finally {
            Files.deleteIfExists(portFile);
        }
    }

    private static String read(DataInputStream in) throws Exception {
        byte[] bytes = new byte[in.readInt()];
        in.readFully(bytes);
        return new String(bytes, StandardCharsets.UTF_8);
    }

    private static void serve(Socket socket, String token) throws Exception {
        DataInputStream in = new DataInputStream(socket.getInputStream());
        DataOutputStream out = new DataOutputStream(socket.getOutputStream());
        if (!read(in).equals(token)) return;
        String tool = read(in);
        String[] args = new String[in.readInt()];
        for (int i = 0; i < args.length; i++) args[i] = read(in);

        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        PrintStream capture = new PrintStream(buffer, true, "UTF-8");
        PrintStream oldOut = System.out, oldErr = System.err;
        int code;
        System.setOut(capture);
        System.setErr(capture);
        try {
            code = run(tool, args, capture);
        } catch (Throwable error) {
            Throwable cause = error.getCause() != null ? error.getCause() : error;
            capture.println(cause);
            code = 1;
        } finally {
            System.setOut(oldOut);
            System.setErr(oldErr);
        }
        byte[] output = buffer.toByteArray();
        out.writeInt(code);
        out.writeInt(output.length);
        out.write(output);
        out.flush();
    }

    private static int run(String tool, String[] args, PrintStream capture) throws Exception {
        switch (tool) {
            case "ping":
                return 0;
            case "javac":
                return javax.tools.ToolProvider.getSystemJavaCompiler().run(null, capture, capture, args);
            case "d8":
                com.android.tools.r8.D8.run(com.android.tools.r8.D8Command.parse(
                        args, com.android.tools.r8.origin.Origin.root()).build());
                return 0;
            case "kotlinc": {
                // args[0] is the compiler's own classpath; one cached loader per compiler.
                ClassLoader loader = kotlinLoaders.get(args[0]);
                if (loader == null) {
                    String[] entries = args[0].split(File.pathSeparator);
                    URL[] urls = new URL[entries.length];
                    for (int i = 0; i < entries.length; i++) urls[i] = new File(entries[i]).toURI().toURL();
                    loader = new URLClassLoader(urls, ClassLoader.getPlatformClassLoader());
                    kotlinLoaders.put(args[0], loader);
                }
                String[] rest = java.util.Arrays.copyOfRange(args, 1, args.length);
                Thread thread = Thread.currentThread();
                ClassLoader previous = thread.getContextClassLoader();
                thread.setContextClassLoader(loader);
                try {
                    Class<?> compiler = loader.loadClass("org.jetbrains.kotlin.cli.jvm.K2JVMCompiler");
                    Method exec = compiler.getMethod("exec", PrintStream.class, String[].class);
                    Object result = exec.invoke(compiler.getDeclaredConstructor().newInstance(), capture, rest);
                    return (Integer) result.getClass().getMethod("getCode").invoke(result);
                } finally {
                    thread.setContextClassLoader(previous);
                }
            }
            default:
                capture.println("unknown tool " + tool);
                return 2;
        }
    }
}
