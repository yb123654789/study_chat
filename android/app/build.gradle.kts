import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
}

val keystoreProps = rootProject.file("keystore/keystore.properties")

android {
    namespace = "com.studybot.app"
    compileSdk = 34
    buildToolsVersion = "34.0.0"

    defaultConfig {
        applicationId = "com.studybot.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 3
        versionName = "0.3.0"
        // 手机只装 arm64；x86_64 留给模拟器自测（发布时再去掉）
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }

    androidResources {
        // SenseVoice 模型 228MB：不压缩，装机后拷到 filesDir 是磁盘速度
        noCompress += listOf("onnx")
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.exists()) {
                val p = Properties().apply { keystoreProps.inputStream().use { load(it) } }
                storeFile = rootProject.file(p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"),
                          "proguard-rules.pro")
            if (keystoreProps.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.activity:activity-ktx:1.8.1")
    implementation("androidx.fragment:fragment-ktx:1.7.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.7.0")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    // 本地语音：sherpa-onnx（SenseVoice 识别 + Silero VAD），官方预编译 AAR
    implementation(files("libs/sherpa-onnx-2.0.1.aar"))
    // P5 笔记：中文 OCR（bundled，模型打进 apk，不依赖 GMS）
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
}
