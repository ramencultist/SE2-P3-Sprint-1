/*
Module Name: settings.gradle.kts
Description: Gradle settings for the detector app: plugin and dependency repositories and the project name

Inputs: None
Outputs: Repository configuration for the build
External Sources: Claude Fable 5.1
Attributions:

Creation 11/5/2026 - Blake Pennel and Claude Fable 5.1:
A description of how and why AI was used: Claude Fable 5.1 used to set up the Gradle project for the detector app
How you validated and revised the AI output: Proofreading, and building the app with gradlew assembleDebug
The challenges or limitations you faced while using AI: None notable
Authors: Blake Pennel
Creation Date: 11/5/2026
*/

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "wazeomatic-detector"
include(":app")
