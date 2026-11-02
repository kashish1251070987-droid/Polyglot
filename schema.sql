CREATE TABLE IF NOT EXISTS students (
  student_id INT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  email VARCHAR(180) NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS courses (
  course_id INT PRIMARY KEY,
  course_name VARCHAR(120) NOT NULL,
  credits INT NOT NULL CHECK (credits > 0)
);
CREATE TABLE IF NOT EXISTS enrollments (
  enrollment_id INT PRIMARY KEY,
  student_id INT NOT NULL,
  course_id INT NOT NULL,
  semester VARCHAR(20) NOT NULL,
  status VARCHAR(20) NOT NULL,
  UNIQUE KEY unique_enrollment (student_id, course_id, semester),
  FOREIGN KEY (student_id) REFERENCES students(student_id),
  FOREIGN KEY (course_id) REFERENCES courses(course_id),
  CHECK (status IN ('active', 'completed', 'withdrawn')),
  INDEX course_status (course_id, status)
);
