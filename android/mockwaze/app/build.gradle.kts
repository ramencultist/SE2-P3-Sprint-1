/*
Module Name: build.gradle.kts
Description: Gradle build script for the mock Waze app module: SDK levels and build settings

Inputs: Plugin versions from the top-level build
Outputs: The mock Waze APK
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to configure the mock Waze app module
How you validated and revised the AI output: Proofreading, and building the APK for the emulator suite
The challenges or limitations you faced while using AI: None notable
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.wazeomatic.mockwaze"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.wazeomatic.mockwaze"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

// No AndroidX on purpose: plain framework views keep the build small and the
// view tree easy to inspect with uiautomator dump.
