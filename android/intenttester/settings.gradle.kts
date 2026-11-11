/*
 * Module Name: Gradle project settings
 * Description: Configures plugin and dependency repositories and includes the app module.
 * 
 * Inputs: Repository declarations, root project name, and app module path.
 * Outputs: Standalone Gradle project structure and dependency resolution settings.
 * External Sources: ChatGPT 6.1 Sol
 * Attributions: Android SDK and Gradle build tooling
 * Creation 11/10/2026 - Drew and ChatGPT:
 * A description of how and why AI was used: AI assisted with creating the tester to category-only Traffic, Police, and Hazard reports, and updating the setup documentation.
 * 
 * How you validated and revised the AI output: Built the APK, installed it on a device. Used the different categories of reports and verified that it worked with the macro on Android.
 * 
 * The challenges or limitations you faced while using AI: Aligning the intent of AI generated content with the purpose of the project and desired categories of Police, Traffic, and Hazard. Attempted to generate other categories/subcategories.
 * Authors: Drew Medlock
 * Creation Date: 11/10/2026
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
rootProject.name = "intenttester"
include(":app")
