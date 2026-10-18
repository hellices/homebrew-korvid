# frozen_string_literal: true

# Official binary components, kept separate from the user's Rust installation.
class KorvidRust < Formula
  desc "Prebuilt Rust toolchain for korvid's Intel macOS builds"
  homepage "https://www.rust-lang.org/"
  url "https://static.rust-lang.org/dist/2026-10-15/rustc-1.99.1-x86_64-apple-darwin.tar.xz"
  sha256 "203feef2c2165d7dda1f30ccf75590f72a6abc52ce259ed1c418de84a6887698"
  license any_of: ["Apache-2.0", "MIT"]

  keg_only "it is a private build toolchain for korvid"

  # Upstream dylibs have no header space for longer absolute install names.
  depends_on arch: :x86_64
  depends_on :macos

  preserve_rpath

  resource "cargo" do
    url "https://static.rust-lang.org/dist/2026-10-15/cargo-1.99.1-x86_64-apple-darwin.tar.xz"
    sha256 "6636dabcec164909d1c6eb9e2c596148787c84f7d3a02ff68a141569077f80b5"
  end

  resource "rust-std" do
    url "https://static.rust-lang.org/dist/2026-10-15/rust-std-1.99.1-x86_64-apple-darwin.tar.xz"
    sha256 "484d5b88b424ddd29df196e1eb9420c8335bb5fac90a3c37140253345a8c65c4"
  end

  def install
    system "./install.sh", "--prefix=#{prefix}", "--disable-ldconfig"
    resources.each do |resource|
      resource.stage do
        system "./install.sh", "--prefix=#{prefix}", "--disable-ldconfig"
      end
    end
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/rustc --version")
    system bin/"cargo", "--version"
    (testpath/"hello.rs").write 'fn main() { println!("korvid toolchain"); }'
    system bin/"rustc", "hello.rs", "-o", "hello"
    assert_equal "korvid toolchain\n", shell_output("./hello")
  end
end
