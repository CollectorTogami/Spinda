if(DEFINED ENV{VIRTUAL_ENV})
      find_program(PYTHON_EXECUTABLE NAMES python PATHS "$ENV{VIRTUAL_ENV}/bin")
endif()
find_package(PythonInterp ${USE_PYTHON_VERSION})
find_package(PythonLibs ${USE_PYTHON_VERSION})
# Keep the interpreter base prefix so embedded Qt/SDL runs can rebuild
# PYTHONHOME without asking the user to export it manually first.
execute_process(
      COMMAND ${PYTHON_EXECUTABLE} -c "import sys; print(sys.base_prefix)"
      OUTPUT_VARIABLE PYTHON_BASE_PREFIX
      OUTPUT_STRIP_TRAILING_WHITESPACE)
execute_process(
      # Preserve the selected venv separately from sys.base_prefix so the
      # embedded Qt runtime can recover the same third-party package set that
      # the host-side tests and tools use.
      COMMAND ${PYTHON_EXECUTABLE} -c "import sys; print(sys.prefix)"
      OUTPUT_VARIABLE PYTHON_VENV_PREFIX
      OUTPUT_STRIP_TRAILING_WHITESPACE)
execute_process(
      COMMAND ${PYTHON_EXECUTABLE} -c "import sysconfig; print(sysconfig.get_paths()['platlib'])"
      OUTPUT_VARIABLE PYTHON_SITE_PACKAGES
      OUTPUT_STRIP_TRAILING_WHITESPACE)
file(TO_CMAKE_PATH "${PYTHON_BASE_PREFIX}" PYTHON_BASE_PREFIX_CMAKE)
file(TO_CMAKE_PATH "${PYTHON_VENV_PREFIX}" PYTHON_VENV_PREFIX_CMAKE)
file(TO_CMAKE_PATH "${PYTHON_SITE_PACKAGES}" PYTHON_SITE_PACKAGES_CMAKE)
list(APPEND DEPENDENCY_LIB ${PYTHON_LIBRARIES})
include_directories(AFTER ${PYTHON_INCLUDE_DIRS})

set(PYTHON_LINK_LIBRARY ${PYTHON_LIBRARY})
if(NOT PYTHON_LINK_LIBRARY)
      set(PYTHON_LINK_LIBRARY ${PYTHON_LIBRARIES})
endif()

get_property(INCLUDE_DIRECTORIES DIRECTORY PROPERTY INCLUDE_DIRECTORIES)
set(INCLUDE_FLAGS)
set(INCLUDE_FLAGS_STR "")
foreach(DIR IN LISTS INCLUDE_DIRECTORIES)
      list(APPEND INCLUDE_FLAGS "-I${DIR}")
      set(INCLUDE_FLAGS_STR "${INCLUDE_FLAGS_STR} \"-I${DIR}\"")
endforeach()
string(REPLACE ";" " " INCLUDE_FLAGS_ENV "${INCLUDE_FLAGS}")

file(GLOB PYTHON_HEADERS ${CMAKE_CURRENT_SOURCE_DIR}/*.h)

add_custom_command(OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/lib.c
                  # Use `cmake -E env` so shell, IDE, and preset builds all feed
                  # the same include flags into the generated CFFI bridge.
                  COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/_builder.py
                  COMMAND ${CMAKE_COMMAND} -E touch ${CMAKE_CURRENT_BINARY_DIR}/lib.c
                  DEPENDS ${PYTHON_HEADERS}
                  DEPENDS ${BINARY_NAME}
                  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/_builder.py)

set_source_files_properties(${CMAKE_CURRENT_BINARY_DIR}/lib.c PROPERTIES GENERATED ON)

file(GLOB PYTHON_SRC ${CMAKE_CURRENT_SOURCE_DIR}/*.c)
set(PYLIB_TYPE STATIC)
if(WIN32)
      # On MinGW, putting the whole Python bridge into the frontend EXE causes
      # pseudo-relocation failures. A DLL keeps the imports small and stable.
      # Linking the Python bridge directly into mgba-sdl.exe pulls Python data
      # imports into the executable and trips MinGW pseudo-relocation at
      # runtime. Build the bridge as a DLL on Windows so the EXE only imports
      # the bridge entry points.
      set(PYLIB_TYPE SHARED)
endif()
add_library(${BINARY_NAME}-pylib ${PYLIB_TYPE} ${CMAKE_CURRENT_BINARY_DIR}/lib.c ${PYTHON_SRC})
set_target_properties(${BINARY_NAME}-pylib PROPERTIES INCLUDE_DIRECTORIES "${CMAKE_BINARY_DIR};${INCLUDE_DIRECTORIES}")
set_target_properties(${BINARY_NAME}-pylib PROPERTIES COMPILE_DEFINITIONS "${OS_DEFINES};${FEATURE_DEFINES};${FUNCTION_DEFINES}")
target_link_libraries(${BINARY_NAME}-pylib PRIVATE ${BINARY_NAME} ${PYTHON_LINK_LIBRARY})
if(PYTHON_BASE_PREFIX_CMAKE)
      target_compile_definitions(${BINARY_NAME}-pylib PRIVATE MGBA_PYTHON_BASE_PREFIX="${PYTHON_BASE_PREFIX_CMAKE}")
endif()
if(PYTHON_VENV_PREFIX_CMAKE)
      target_compile_definitions(${BINARY_NAME}-pylib PRIVATE MGBA_PYTHON_VENV_PREFIX="${PYTHON_VENV_PREFIX_CMAKE}")
endif()
if(PYTHON_SITE_PACKAGES_CMAKE)
      target_compile_definitions(${BINARY_NAME}-pylib PRIVATE MGBA_PYTHON_SITE_PACKAGES="${PYTHON_SITE_PACKAGES_CMAKE}")
endif()
if(WIN32)
      target_compile_definitions(${BINARY_NAME}-pylib PRIVATE MGBA_PYTHON_DLL INTERFACE MGBA_PYTHON_IMPORT)
      set_target_properties(${BINARY_NAME}-pylib PROPERTIES
            WINDOWS_EXPORT_ALL_SYMBOLS ON
            RUNTIME_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}"
            LIBRARY_OUTPUT_DIRECTORY "${PROJECT_BINARY_DIR}")
endif()

add_custom_target(${BINARY_NAME}-py ALL
                  # Mirror the same environment in the packaging/tests path so
                  # the built wheel matches the in-tree executable behavior.
                  COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} LIBDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} setup.py build -b ${CMAKE_CURRENT_BINARY_DIR}
                  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                  DEPENDS ${BINARY_NAME}
                  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/setup.py
                  DEPENDS ${PYTHON_HEADERS}
                  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/_builder.py
                  DEPENDS ${BINARY_NAME}-pylib)

add_custom_target(${BINARY_NAME}-py-install
                  COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} LIBDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} setup.py install -b ${CMAKE_CURRENT_BINARY_DIR}
                  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                  DEPENDS ${BINARY_NAME}-py)

add_custom_target(${BINARY_NAME}-py-develop
                  COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} LIBDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} setup.py develop -b ${CMAKE_CURRENT_BINARY_DIR}
                  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                  DEPENDS ${BINARY_NAME}-py)

add_custom_target(${BINARY_NAME}-py-bdist
                  COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} LIBDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} setup.py bdist_wheel -b ${CMAKE_CURRENT_BINARY_DIR}
                  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                  DEPENDS ${BINARY_NAME}-py)

file(GLOB TESTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/*/test_*.py)
foreach(TEST IN LISTS TESTS)
	if(APPLE)
		set(PATH DYLD_LIBRARY_PATH)
	elseif(WIN32)
		set(PATH PATH)
	else()
		set(PATH LD_LIBRARY_PATH)
	endif()
	string(REGEX REPLACE "${CMAKE_CURRENT_SOURCE_DIR}/tests/(.*/)?test_" "" TEST_NAME "${TEST}")
	string(REPLACE ".py" "" TEST_NAME "${TEST_NAME}")
	add_test(NAME python-${TEST_NAME}
               COMMAND ${CMAKE_COMMAND} -E env BINDIR=${PROJECT_BINARY_DIR} LIBDIR=${PROJECT_BINARY_DIR} "CPPFLAGS=${INCLUDE_FLAGS_ENV}" ${PYTHON_EXECUTABLE} setup.py build -b ${CMAKE_CURRENT_BINARY_DIR} pytest --extras --addopts ${TEST}
               WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
	set_tests_properties(python-${TEST_NAME} PROPERTIES ENVIRONMENT "${PATH}=${PROJECT_BINARY_DIR};BINDIR=${PROJECT_BINARY_DIR};LIBDIR=${PROJECT_BINARY_DIR};CPPFLAGS=${INCLUDE_FLAGS_ENV}")
endforeach()
