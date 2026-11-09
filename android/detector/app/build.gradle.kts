/*
Module Name: build.gradle.kts
Description: Gradle build script for the detector app module: SDK levels, ONNX Runtime and CameraX dependencies and tests

Inputs: Plugin versions from the top-level build
Outputs: The detector APK and unit test tasks
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to configure the app module and its dependencies
How you validated and revised the AI output: Proofreading, building the APK and running gradlew test
The challenges or limitations you faced while using AI: Choosing dependency versions that work together on API 29 to 35
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.wazeomatic.detector"
    compileSdk = 35
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "com.wazeomatic.detector"
        minSdk = 29
        targetSdk = 35
        versionCode = 1
        versionName = "0.1"
        ndk {
            // phones + the x86_64 emulator; keeps onnxruntime from bloating the apk
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
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
    androidResources {
        // onnx files are already dense, compressing them only slows down loading
        noCompress += listOf("onnx")
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-service:2.8.7")
    implementation("androidx.camera:camera-core:1.4.1")
    implementation("androidx.camera:camera-camera2:1.4.1")
    implementation("androidx.camera:camera-lifecycle:1.4.1")
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.20.0")

    testImplementation("junit:junit:4.13.2")
    // android.jar only ships stubs of org.json, the real one is needed on the JVM
    testImplementation("org.json:json:20240303")
}

// Copies exported models from <repo>/models into the app assets.
// Override the source file names with e.g. -PcocoModel=coco_yolo11n.int8.onnx
val repoModels = rootProject.file("../../models")
val hazardModel = (findProperty("hazardModel") as String?) ?: "hazard_yolo11n.onnx"
val cocoModel = (findProperty("cocoModel") as String?) ?: "coco_yolo11n.onnx"
// second hazard model fused on the device (ensemble setting); "" (the default since the v3 model)
// ships the single v3 nano. The older 4-class models no longer help it (docs/model-research.md);
// -PhazardModel2=hazard_yolo11s_v3.onnx fuses the v3 small where the phone can afford it.
val hazardModel2 = (findProperty("hazardModel2") as String?) ?: ""

val syncModels by tasks.registering(Copy::class) {
    description = "Copy models/*.onnx and label files into app/src/main/assets"
    doFirst {
        if (hazardModel2.isBlank()) {
            // a previous build with a second model leaves its assets behind; Copy never deletes
            layout.projectDirectory.file("src/main/assets/hazard2.onnx").asFile.delete()
            layout.projectDirectory.file("src/main/assets/hazard2_labels.txt").asFile.delete()
        }
    }
    from(repoModels) {
        include(hazardModel)
        rename { "hazard.onnx" }
    }
    from(repoModels) {
        include(cocoModel)
        rename { "coco.onnx" }
    }
    // optional second hazard model for on-device fusion (-PhazardModel2=hazard_yolo11s_v3.onnx)
    if (hazardModel2.isNotBlank()) {
        from(repoModels) {
            include(hazardModel2)
            rename { "hazard2.onnx" }
        }
        from(repoModels) {
            include(hazardModel2.removeSuffix(".onnx") + ".labels.txt")
            rename { "hazard2_labels.txt" }
        }
    }
    // export_onnx.py writes <model>.labels.txt next to each model
    from(repoModels) {
        include(hazardModel.removeSuffix(".onnx") + ".labels.txt")
        rename { "hazard_labels.txt" }
    }
    from(repoModels) {
        include(cocoModel.removeSuffix(".onnx") + ".labels.txt")
        rename { "coco_labels.txt" }
    }
    into(layout.projectDirectory.dir("src/main/assets"))
}

tasks.named("preBuild") { dependsOn(syncModels) }
