# youtubedl-android unzips its Python with commons-compress, which creates the zip extra
# field classes by reflection: R8 must keep their constructors.
-keep class * implements org.apache.commons.compress.archivers.zip.ZipExtraField {
  <init>();
}
